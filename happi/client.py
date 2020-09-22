import collections
import configparser
import inspect
import itertools
import logging
import os
import re
import sys
import time as ttime
import warnings

from . import containers
from .backends import BACKENDS, DEFAULT_BACKEND
from .backends.core import _Backend
from .item import HappiItem
from .errors import DatabaseError, EntryError, SearchError
from .loader import from_container

logger = logging.getLogger(__name__)


def _looks_like_database(obj):
    """
    Does the given object look like a backend we can use or does it inherit
    from _Backend
    """
    return (isinstance(obj, _Backend) or
            all(
                hasattr(obj, attr) for attr in (
                    'find', 'all_devices', 'delete', 'save')
                )
            )


class SearchResult(collections.abc.Mapping):
    '''
    A single search result from ``Client.search``

    This result can be keyed for metadata as in::
        result['name']

    The HappiItem can be readily retrieved::
        result.item

    Or the object may be instantiated::
        result.get()

    Attributes
    ----------
    item : happi.HappiItem
        The container
    metadata : dict
        The HappiItem metadata
    '''

    def __init__(self, client, device):
        self._device = device
        self._instantiated = None
        self.client = client
        self.metadata = device.post()

    @property
    def device(self):
        warnings.warn('SearchResult.device deprecated, use SearchResult.item')
        return self.item

    @property
    def item(self):
        if self._device is None:
            self._device = self.client.find_device(**self.metadata)
        return self._device

    def get(self, attach_md=True, use_cache=True, threaded=False):
        '''(get) ''' + from_container.__doc__
        if self._instantiated is None:
            self._instantiated = from_container(
                self.item, attach_md=attach_md, use_cache=use_cache,
                threaded=threaded
            )
        return self._instantiated

    def __getitem__(self, item):
        return self.metadata[item]

    def __iter__(self):
        yield from self.metadata

    def __len__(self):
        return len(self.metadata)

    def __repr__(self):
        return (
            f'{self.__class__.__name__}(client={self.client}, '
            f'metadata={self.metadata})'
        )


class Client(collections.abc.Mapping):
    """
    The client to control the contents of the Happi Database

    Parameters
    ----------
    database : happi.backends.Backend
        A already instantiated backend

    kwargs:
        Passed to the `db_type` backend

    Raises
    -----
    DatabaseError:
        Raised if the Client fails to instantiate the Database
    """
    # HappiItem information
    _client_attrs = ['_id', 'type', 'creation', 'last_edit']
    _id_key = 'name'
    _results_wrap_class = SearchResult

    def __init__(self, database=None, **kwargs):
        # Use supplied backend
        if database:
            self.backend = database
            if not _looks_like_database(database):
                raise ValueError(f'{database!r} does not look like a database;'
                                 f' expecting an instantiated happi backend')
        # Load database
        else:
            logger.debug("No database given, using '%s'", DEFAULT_BACKEND)
            try:
                self.backend = DEFAULT_BACKEND(**kwargs)
            except Exception as exc:
                raise DatabaseError(
                    f"Failed to instantiate a {DEFAULT_BACKEND} backend"
                ) from exc

    def find_document(self, **kwargs):
        """
        Load a device document from the database

        If multiple matches are found, a single document will be returned to
        the user. How the database will choose to select this device is based
        on each individual implementation

        Parameters
        ----------
        kwargs :
            Add information to locate the device in keyword pairs

        Returns
        -------
        document : dict
            A dict that matches the specified information.

        Raises
        ------
        SearchError:
            If no document with the given information is found

        See Also
        --------
        :meth:`.find_device`, :meth:`.search`
        """
        if len(kwargs) == 0:
            raise SearchError('No information pertinent to device given')

        matches = list(itertools.islice(self.backend.find(kwargs), 1))
        if not matches:
            raise SearchError(
                'No device information found that matches the search criteria')
        return matches[0]

    def create_device(self, device_cls, **kwargs):
        """
        Create a new device

        Parameters
        ----------
        device_cls : :class:`.HappiItem` or name of class
            The Device HappiItem to instantiate

        kwargs :
            Information to pass through to the device, upon initialization

        Returns
        -------
        device :
            An instantiated version of the device

        Raises
        ------
        TypeError:
            If the provided class is not a subclass of :class:`.HappiItem`


        Example
        -------
        .. code::

            device = client.create_device(Device,   name='my_device' ...)
            device = client.create_device('Device', name='my_device',...)

        """
        # If specified by a string
        if device_cls in containers.registry:
            device_cls = containers.registry[device_cls]

        # Check that this is a valid HappiItem
        if isinstance(device_cls, str):
            raise TypeError(
                f'The device class {device_cls!r} is not in the registry'
            )

        if (not inspect.isclass(device_cls) or
                not issubclass(device_cls, HappiItem)):
            raise TypeError(f'{device_cls!r} is not a subclass of HappiItem')

        device = device_cls(**kwargs)
        # Add the method to the device

        def save_device():
            self.add_device(device)

        device.save = save_device
        return device

    def add_device(self, device):
        """
        Add a new device into the database

        Parameters
        ----------
        device : :class:`.HappiItem`
            The device to store in the database

        Raises
        ------
        EntryError:
            If all of the mandatory information for the device has not been
            specified or there is already a device with that ``id`` in the
            database
        """
        logger.info("Storing device %r ...", device)
        _id = self._store(device, insert=True)
        logger.info('HappiItem %r has been succesfully added to the '
                    'database', device)

        def save_device():
            self._store(device, insert=False)

        device.save = save_device
        return _id

    def find_device(self, **post):
        """
        Used to query the database for an individual HappiItem

        If multiple devices are found, only the first is returned

        Parameters
        ----------
        post :
            Information to pertinent to the device

        Raises
        ------
        SearchError
            If no match for the given information is found

        Returns
        -------
        device : :class:`.HappiItem`
            A device that matches the characteristics given
        """
        logger.debug("Gathering information about the device ...")
        doc = self.find_document(**post)
        # Instantiate HappiItem
        logger.debug("Instantiating device based on found information ...")
        try:
            device = self.create_device(doc['type'], **doc)
        except (KeyError, TypeError) as exc:
            raise EntryError('The information relating to the device class '
                             'has been modified to the point where the object '
                             'can not be initialized, please load the '
                             'corresponding document') from exc

        # Add the save method to the device
        device.save = lambda: self._store(device, insert=False)
        return device

    def load_device(self, use_cache=True, **post):
        """
        Find a device in the database and instantiate it

        Essentially a shortcut for:

        .. code:: python

            container = client.find_device(name='...')
            device = from_container(container)

        Parameters
        ----------
        post:
            Passed to :meth:`.Client.find_device`

        use_cache: bool, optional
            Choice to use a cached device. See :meth:`.from_container` for more
            information

        Returns
        -------
        obj:
            Instantiated object
        """
        cntr = self.find_device(**post)
        return from_container(cntr, use_cache=use_cache)

    def validate(self):
        """
        Validate all of the devices in the database by attempting to initialize
        them and asserting their mandatory information is present. Information
        is written to the logger

        Returns
        -------
        ids : list
            List of device ids that have failed verification
        """
        bad = list()
        logger.debug('Loading database to validate contained devices ...')
        for post in self.backend.all_devices:
            # Try and load device based on database info
            try:
                # HappiItem identification
                _id = post[self._id_key]
                logger.debug('Attempting to initialize %s...', _id)
                # Load HappiItem
                device = self.find_device(**post)
                logger.debug('Attempting to validate ...')
                self._validate_device(device)
            except KeyError:
                logger.error("Post has no id  %s", post)
            # Log all generated exceptions
            except Exception as e:
                logger.warning("Failed to validate %s because %s", _id, e)
                bad.append(_id)
            # Report successes
            else:
                logger.debug('Successfully validated %s', _id)
        return bad

    @property
    def all_devices(self):
        """
        A list of all contained devices
        """
        warnings.warn('Client.all_devices deprecated, use all_items')
        return self.all_items

    @property
    def all_items(self):
        """
        A list of all contained devices
        """
        return [res.item for res in self.search()]

    def __getitem__(self, key):
        'Get a device ID'
        try:
            device = self.find_device(**self.backend.get_by_id(key))
        except Exception as ex:
            raise KeyError(key) from ex

        return SearchResult(client=self, device=device)

    def __iter__(self):
        for info in self.backend.find({}):
            yield info['_id']

    def __len__(self):
        return len(self.all_items)

    def _get_search_results(self, items, *, wrap_cls=None):
        '''
        Return search results to the user, optionally wrapping with a class
        '''
        wrap_cls = wrap_cls or self._results_wrap_class
        results = []
        for info in items:
            try:
                result = wrap_cls(client=self, device=self.find_device(**info))
                results.append(result)
            except Exception as exc:
                logger.warning('Entry for %s is malformed (%s). Skipping.',
                               info['name'], exc)
        return results

    def search_range(self, key, start, end=None, **kwargs):
        """
        Search the database for a device or devices

        Parameters
        -----------
        key : str
            Database key to search

        start : float, optional
            Minimum beamline position to include devices

        end : float, optional
            Maximum beamline position to include devices

        **kwargs
            Information to filter through the database structured as key, value
            pairs for the desired pieces of EntryInfo

        Returns
        -------
        Either a list of devices or dictionaries

        Example
        -------
        .. code::

            gate_valves = client.search_range('z', 0, 100, type='Valve')
            hxr_valves  = client.search_range('z', 0, 100, type='Valve',
                                              beamline='HXR')
        """
        items = self.backend.find_range(key, start=start, stop=end,
                                        to_match=kwargs)
        return self._get_search_results(items)

    def search(self, **kwargs):
        """
        Search the database for a device or devices

        Parameters
        -----------
        **kwargs
            Information to filter through the database structured as key, value
            pairs for the desired pieces of EntryInfo

        Returns
        -------
        res : list of SearchResult
            The search results

        Example
        -------
        .. code::

            gate_valves = client.search(type='Valve')
            hxr_valves  = client.search(type='Valve', beamline='HXR')
        """
        items = self.backend.find(kwargs)
        return self._get_search_results(items)

    def search_regex(self, flags=re.IGNORECASE, **kwargs):
        """
        Search the database for a device or devices

        Parameters
        -----------
        flags : int, optional
            Defaulting to ``re.IGNORECASE``, these flags are used for the
            regular expressions passed in.

        **kwargs
            Information to filter through the database structured as key, value
            pairs for the desired pieces of EntryInfo.  Every value is allowed
            to contain a Python regular expression.

        Returns
        -------
        Either a list of devices or dictionaries

        Example
        -------
        .. code::

            gate_valves = client.search_regex(beamline='Valve.*')
            three_valves = client.search_regex(_id='VALVE[123]')
        """
        items = self.backend.find_regex(kwargs, flags=flags)
        return self._get_search_results(items)

    def export(self, path=sys.stdout, sep='\t', attrs=None):
        """
        Export the contents of the database into a text file

        Parameters
        ----------
        path : File Handle
            File-like object to save text file

        sep : str
            Separator to place inbetween columns of information

        attrs : iterable
            Attributes to include, these will be a list of values
        """
        # Load documents
        devs = self.all_items
        logger.info('Creating file at %s ...', path)
        # Load device information
        with path as f:
            for dev in devs:
                try:
                    f.write(sep.join([getattr(dev, attr)
                                      for attr in attrs])
                            + '\n')
                except KeyError as e:
                    logger.error("HappiItem %s was missing attribute %s",
                                 dev.name, e)

    def remove_device(self, device):
        """
        Remove a device from the database

        Parameters
        ----------
        device : :class:`.HappiItem`
            HappiItem to be removed from the database
        """
        # HappiItem Check
        if not isinstance(device, HappiItem):
            raise ValueError("Must supply an object of type `HappiItem`")
        logger.info("Attempting to remove %r from the "
                    "collection ...", device)
        # Check that device is in the database
        _id = getattr(device, self._id_key)
        self.backend.delete(_id)

    def _validate_device(self, device):
        """
        Validate that a device has all of the mandatory information
        """
        logger.debug('Validating device %r ...', device)

        # Check type
        if not isinstance(device, HappiItem):
            raise ValueError('{!r} is not a subclass of '
                             'HappiItem'.format(device))
        logger.debug('Checking mandatory information has been entered ...')
        # Check that all mandatory info has been entered
        missing = [info.key for info in device.entry_info
                   if not info.optional and
                   info.default == getattr(device, info.key)]
        # Abort initialization if missing mandatory info
        if missing:
            raise EntryError('Missing mandatory information ({}) for {}'
                             ''.format(', '.join(missing),
                                       device.__class__.__name__))
        logger.debug('HappiItem %r has been validated.', device)

    def _store(self, device, insert=False):
        """
        Store a document in the database

        Parameters
        ----------
        post : :class:`.HappiItem`
            HappiItem to save

        insert : bool, optional
            Set to True if this is a new entry

        Raises
        ------
        DuplicateError:
            When _id already exists

        EntryError:
            If the device doesn't the correct information

        Todo
        ----
        Enforce parent is an already entered name
        """
        logger.debug('Loading a device into the collection ...')

        # Validate device is ready for storage
        self._validate_device(device)
        # Grab information from device
        post = device.post()
        # save the old name in case the user is trying to
        # change the 'name' of an entry
        the_old_name = post.get('_id', None)
        # Store creation time
        creation = post.get('creation', ttime.ctime())
        # Clean supplied information
        [post.pop(key) for key in self._client_attrs if key in post]
        # Note that device has some unrecognized metadata
        for key in post.keys():
            if key not in device.info_names:
                logger.debug("HappiItem %r defines an extra piece of "
                             "information under the keyword %s",
                             device, key)
        # Add metadata from the Client Side

        tpe = containers.registry.entry_for_class(device.__class__)
        post.update({'type': tpe,
                     'creation': creation,
                     'last_edit': ttime.ctime()})

        # Find id
        try:
            _id = post[self._id_key]
        except KeyError:
            raise EntryError('HappiItem did not supply the proper information '
                             'to interface with the database, missing {}'
                             ''.format(self._id_key))
        # In case we want to update the name of an entry
        # We want to add a new entry, and delete the old one
        if the_old_name and the_old_name != post[self._id_key]:
            # Store information for the new entry
            logger.info('Saving new entry %s ...', _id)
            self.backend.save(_id, post, insert=True)
            # Remove the information for the old entry
            logger.info('Removing old entry %s ...', the_old_name)
            self.backend.delete(the_old_name)
        else:
            # Store information
            logger.info('Adding / Modifying information for %s ...', _id)
            self.backend.save(_id, post, insert=insert)
        return _id

    @classmethod
    def from_config(cls, cfg=None):
        """
        Create a client from a configuration file specification

        Configuration files looking something along the lines of:

        .. code::

            [DEFAULT]
            path=path/to/my/db.json

        All key value pairs will be passed directly into backend construction
        with the exception of the key ``backend`` which can be used to specify
        a specific type of backend if this differs from the configured default.

        Parameters
        ----------
        cfg: str, optional
            Path to a configuration file. If not entered, :meth:`.find_config`
            will be use.
        """
        # Find a configuration file
        if not cfg:
            cfg = cls.find_config()

        if not os.path.exists(cfg):
            raise RuntimeError(f'happi configuration file not found: {cfg!r}')

        # Parse configuration file
        cfg_parser = configparser.ConfigParser()
        cfg_file = cfg_parser.read(cfg)
        logger.debug("Loading configuration file at %r", cfg_file)
        db_kwargs = cfg_parser['DEFAULT']
        # If a backend is specified use it, otherwise default
        if 'backend' in db_kwargs:
            db_str = db_kwargs.pop('backend')
            try:
                backend = BACKENDS[db_str]
            except KeyError:
                raise RuntimeError(
                    f'Happi backend {db_str!r} unavailable'
                ) from None
        else:
            backend = DEFAULT_BACKEND

        logger.debug("Using Happi backend %r with kwargs %r",
                     backend, db_kwargs)
        # Create our database with provided kwargs
        try:
            database = backend(**db_kwargs)
            return cls(database=database)
        except Exception as ex:
            raise RuntimeError(
                f'Unable to instantiate the client. Please verify that '
                f'your HAPPI_CFG points to the correct file and has '
                f'the required configuration settings. In {cfg!r}, found '
                f'settings: {dict(db_kwargs)}.'
            ) from ex

    @staticmethod
    def find_config():
        """
        Search for a ``happi`` configuration file

        We first query the environment variable ``$HAPPI_CFG`` to see if this
        points to a specific configuration file. If this is not present, the
        variable set by ``$XDG_CONFIG_HOME`` or if  that is not set
        ``~/.config``

        Returns
        -------
        path: str
            Absolute path to configuration file

        Raises
        ------
        EnvironmentError:
            If no configuration file can be found by the methodology detailed
            above
        """
        # Point to with an environment variable
        if os.environ.get('HAPPI_CFG', False):
            happi_cfg = os.environ.get('HAPPI_CFG')
            logger.debug("Found $HAPPI_CFG specification for Client "
                         "configuration at %s", happi_cfg)
            return happi_cfg
        # Search in the current directory and home directory
        else:
            config_dir = (os.environ.get('XDG_CONFIG_HOME')
                          or os.path.expanduser('~/.config'))
            logger.debug('Searching for Happi config in %s', config_dir)
            for path in ('.happi.cfg', 'happi.cfg'):
                full_path = os.path.join(config_dir, path)
                if os.path.exists(full_path):
                    logger.debug("Found configuration file at %r", full_path)
                    return full_path
        # If found nothing
        raise EnvironmentError("No happi configuration file found. "
                               "Check HAPPI_CFG.")

    def choices_for_field(self, field):
        """
        List all choices for a given field

        Parameters
        ----------
        field : string
            search field to list all possible choices for
            i.e 'beamline', 'name', 'z', 'prefix', etc.

        Raises
        ------
        SearchError
            If no devices in the database have an entry for the given field

        Returns
        -------
        field_choices : list
            list of choices for a given field that are in the database
        """
        field_choices = set()
        for dev in self.all_items:
            try:  # Want to ignore error if 'dev' doesn't have 'field'
                choice = getattr(dev, field)
                field_choices.add(choice)
            except AttributeError:
                pass
        if len(field_choices) == 0:
            raise SearchError('No entries found with given field')
        return field_choices
