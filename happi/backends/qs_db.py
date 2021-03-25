"""
Backend implementation for parsing the LCLS Questionnaire.
"""
import functools
import logging
import re
from typing import Optional

from psdm_qs_cli import QuestionnaireClient

from ..errors import DatabaseError
from .json_db import JSONBackend

logger = logging.getLogger(__name__)


class RequiredKeyError(KeyError):
    """Required key not found in questionnaire."""
    ...


def _create_motor_callable(name, beamline, info):
    """Create a motor entry."""
    container = 'pcdsdevices.happi.containers.Motor'
    class_name = None
    kwargs = {'name': '{{name}}'}
    prefix = info['pvbase']
    if info.get('stageidentity') == 'Beckhoff':
        class_name = 'pcdsdevices.device_types.BeckhoffAxis'
    return create_entry(name, beamline, prefix, kwargs, container,
                        info, class_name)


def _create_trig_callable(name, beamline, info):
    """Create a trigger entry."""
    container = 'pcdsdevices.happi.containers.Trigger'
    kwargs = {'name': '{{name}}'}
    prefix = info['pvbase']
    return create_entry(name, beamline, prefix, kwargs, container, info)


def _create_ai_ao_callable(name, beamline, info):
    """Create an acrommag channel entry."""
    container = 'pcdsdevices.happi.containers.Acromag'
    class_name = 'pcdsdevices.device_types.AcromagChannel'
    prefix = info['pvbase']
    ch = info.get('channel')
    if not ch:
        raise RequiredKeyError('Unable to create an acromag input channel '
                               'entry without a channel')
    name_prefix = 'ai_' if ':ai' in prefix else 'ao_'
    name = f'{name_prefix}{ch}'
    kwargs = {'name': '{{name}}', 'channel': ch}
    return create_entry(name, beamline, prefix, kwargs, container,
                        info, class_name)


def _create_mpod_callable(name, beamline, info):
    """Create the MPOD entry"""
    container = 'pcdsdevices.happi.containers.LCLSItem'
    class_name = 'pcdsdevices.device_types.MPOD'
    prefix = info['pvname']
    kwargs = {'name': '{{name}}'}
    return create_entry(name, beamline, prefix, kwargs, container,
                        info, class_name)


def create_entry(name, beamline, prefix, kwargs, container,
                 info, class_name=None):
    """
    Create a happi_entry.

    Parameters
    ----------
    name : str
        Item name.
    beamline : str
        The beamline with which to associate the entry.
    prefix : str
        Epics base PV.
    kwargs : dict
        Information to pass through to the device, upon initialization
    class_name : str
        The class name to report in the new entry.
    container : str
        The container name to report in the new entry.
    info : dict
        Device information from `_translate_items`.
    """

    entry = {
            '_id': name,
            'active': True,
            'args': ['{{prefix}}'],
            'beamline': beamline,
            'kwargs': kwargs,
            'lightpath': False,
            'name': name,
            'prefix': prefix,
            'type': container,
            **info,
    }
    if class_name is not None:
        entry['device_class'] = class_name

    return entry


# Map of (questionnaire type) to:
#   1. Device class (or factory function) and
#   2. Happi container


DEFAULT_TRANSLATIONS = {
    'motors': _create_motor_callable,
    'trig': _create_trig_callable,
    'ao': _create_ai_ao_callable,
    'ai': _create_ai_ao_callable,
    'ps-mpod': _create_mpod_callable,
}


class QuestionnaireHelper:
    def __init__(self, client: QuestionnaireClient):
        self._client = client
        self._experiment = None
        self.experiment_to_proposal = client.getExpName2URAWIProposalIDs()

    def __repr__(self) -> str:
        try:
            return (
                f'<{self.__class__.__name__} experiment={self.experiment} '
                f'run_number={self.run_number} proposal={self.proposal} '
                f'beamline={self.beamline}>'
            )
        except Exception:
            return f'<{self.__class__.__name__} experiment={self.experiment}>'

    @property
    def experiment(self) -> str:
        """The experiment name."""
        return self._experiment

    @experiment.setter
    def experiment(self, experiment: str):
        self._experiment = experiment

        # Proposals are per-experiment: clear the cache.
        self.get_proposal_list.cache_clear()
        self.get_run_details.cache_clear()

    @property
    def proposal(self):
        """Get the proposal number for the configured experiment."""
        if self.experiment is None:
            raise RuntimeError('Experiment unset')

        try:
            return self.experiment_to_proposal[self.experiment]
        except KeyError:
            # Rare case for debug/daq experiments, roll with it for now
            return self.experiment

    @property
    def run_number(self):
        """Get the run number from the experiment."""
        if self.experiment is None or len(self.experiment) <= 2:
            raise RuntimeError(f'Experiment invalid: {self.experiment}')

        run_number = self.experiment[-2:]
        return f'run{run_number}'

    @functools.lru_cache()
    def get_proposal_list(self) -> dict:
        """
        Get the proposal list (a dict, really) for the configured experiment.

        Raises
        ------
        DatabaseError
        """

        run_number = self.run_number
        try:
            logger.debug("Requesting list of proposals in %s", run_number)
            return self._client.getProposalsListForRun(run_number)
        except KeyError as ex:
            # Invalid proposal id for this run
            raise DatabaseError(
                f'Unable to find proposal {self.proposal}'
            ) from ex
        except Exception as ex:
            # Find if our exception gave an HTTP status code and interpret it
            status_code = ex.args[1] if len(ex.args) >= 2 else ''
            if status_code == 500:
                # No information found from run
                reason = f'No run id found for {run_number}'
            elif status_code == 401:
                # Invalid credentials
                reason = 'Invalid credentials'
            else:
                # Unrecognized error
                reason = 'Unable to find run information'
            raise DatabaseError(reason) from ex

    @property
    def beamline(self) -> str:
        """
        Determine the beamline from a proposal + run_number.

        Returns
        -------
        beamline : str
        """

        proposals = self.get_proposal_list()
        return proposals[self.proposal]['Instrument']

    @functools.lru_cache()
    def get_run_details(self) -> dict:
        """Get details of the run in a raw dictionary."""
        return self._client.getProposalDetailsForRun(
            self.run_number, self.proposal
        )

    def as_happi_database(self, translations=None) -> dict:
        """
        Based on the current experiment, generate a happi database.

        Parameters
        ----------
        translations : dict, optional
            Translations to use when converting questionnaire items.

        Returns
        -------
        db : dict
            The happi JSON-backend-compatible dictionary.
        """

        return self.to_database(
            beamline=self.beamline,
            run_details=self.get_run_details(),
            translations=translations,
        )

    @staticmethod
    def _translate_items(run_details: dict, table_name: str) -> dict:
        """
        Translate flat questionnaire items into nested dictionaries.

        Parameters
        ----------
        run_details : dict
            The run detail dictionary, from `get_run_details`.

        table_name : str
            The table name (e.g., 'motors' of 'pcdssetup-motors-1-name').

        Returns
        -------
        device_info : dict
        """

        pattern = re.compile(rf'pcdssetup-{table_name}-(\d+)-(\w+)')

        devices = {}
        for field, value in run_details.items():
            match = pattern.match(field)
            if match:
                device_number, name = match.groups()

                if device_number not in devices:
                    devices[device_number] = {}

                # Add the key information to the specific device dictionary
                devices[device_number][name] = value

        return devices

    @staticmethod
    def _create_db_item(info: dict,
                        beamline: str,
                        method_call,
                        ) -> dict:
        """
        Create one database entry given translated questionnaire information.

        Parameters
        ----------
        info : dict
            Device information from `_translate_items`.
        beamline : str
            The beamline with which to associate the entry.
        class_name : str
            The class name to report in the new entry.
        container : str
            The container name to report in the new entry.

        Returns
        -------
        happi_entry : dict
        """

        # Shallow-copy to not modify the original:
        info = dict(info)

        name = info.pop('name', None)
        if not name:
            raise RequiredKeyError('Unable to create an item without a name')

        # There are some easy mistakes we can correct for, otherwise
        # happi validation will fail.

        # 1. A capitalized name:
        name = name.lower()
        entry = method_call(name, beamline, info)

        # Empty strings from the Questionnaire make for invalid entries:
        for key in {'prefix', 'name'}:
            if not entry.get(key):
                raise RequiredKeyError(
                    f"Unable to create an item without key {key}"
                )
        return entry

    @staticmethod
    def to_database(beamline: str,
                    run_details: dict,
                    *,
                    translations: Optional[dict] = None
                    ) -> dict:
        """
        Translate a set of run details into a happi-compatible dictionary.

        Parameters
        ----------
        run_details : dict
            The run detail dictionary, from `get_run_details`.
        beamline : str
            The beamline with which to associate the entry.
        translations : dict, optional
            Translations to use when converting questionnaire items.

        Returns
        -------
        db : dict
            The happi JSON-backend-compatible dictionary.
        """

        happi_db = {}
        if translations is None:
            translations = DEFAULT_TRANSLATIONS

        for table_name, translation in translations.items():
            devices = QuestionnaireHelper._translate_items(
                run_details, table_name)

            if not devices:
                logger.info(
                    "No device information found under '%s'", table_name
                )
                continue

            for device_number, item_info in devices.items():
                logger.debug(
                    '[%s:%s] Found %s', table_name, device_number, item_info
                )
                try:
                    entry = QuestionnaireHelper._create_db_item(
                        info=item_info,
                        beamline=beamline,
                        method_call=translation
                    )
                except RequiredKeyError:
                    logger.debug(
                        'Missing key for %s:%s', table_name, device_number,
                        exc_info=True
                    )
                except Exception as ex:
                    logger.warning(
                        'Failed to create a happi database entry from the '
                        'questionnaire device: %s:%s. %s: %s',
                        table_name, device_number, ex.__class__.__name__, ex,
                    )
                else:
                    identifier = entry['_id']
                    if identifier in happi_db:
                        logger.warning(
                            'Questionnaire name clash: %s (was: %s now: %s)',
                            identifier, happi_db[identifier], entry
                        )
                    happi_db[identifier] = entry

        return happi_db


class QSBackend(JSONBackend):
    """
    Questionniare Backend.

    This backend connects to the LCLS questionnaire and looks at items with the
    key pattern pcds-{}-setup-{}-{}. These fields are then combined and turned
    into proper happi items. The translation of table name to `happi.HappiItem`
    is determined by the :attr:`.translations` dictionary.  The beamline is
    determined by looking where the proposal was submitted.

    Unlike the other backends, this one is read-only. All changes to the device
    information should be done via the web interface. Finally, in order to
    avoid duplicating any code needed to search the device database, the
    `QSBackend` inherits directly from `JSONBackend`. Many of the methods are
    unmodified with exception being that this backend merely searches through
    an in-memory dictionary whereas the `JSONBackend` reads from a file before
    searches.

    Parameters
    ----------
    expname : str
        The experiment name from the elog, e.g. xcslp1915.
    url : str, optional
        Provide a base URL for the Questionnaire. If left as None the
        appropriate URL will be chosen based on your authentication method.
    use_kerberos : bool, optional
        Use a Kerberos ticket to login to the Questionnaire. This is the
        default authentication method.
    user : str, optional
        A username for ws_auth sign-in. If not provided the current login name
        is used.
    pw : str, optional
        A password for ws_auth sign-in. If not provided a password will be
        requested.
    """

    translations = DEFAULT_TRANSLATIONS

    def __init__(self, expname, *, url=None, use_kerberos=True, user=None,
                 pw=None):
        # Create our client and gather the raw information from the client
        self._client = QuestionnaireClient(
            url=url, use_kerberos=use_kerberos, user=user, pw=pw
        )

        self.db = self._initialize_database(expname)

    def _initialize_database(self, experiment):
        """Initialize and convert the questionnaire."""
        try:
            self.experiment = experiment
            self.helper = QuestionnaireHelper(self._client)
            self.helper.experiment = experiment
            return self.helper.as_happi_database(
                translations=self.translations
            )
        except Exception:
            logger.error('Failed to load the questionnaire', exc_info=True)
            return {}

    def initialize(self):
        """Can not initialize a new Questionnaire entry from API."""
        raise NotImplementedError("The Questionnaire backend is read-only")

    def load(self):
        """Return the structured dictionary of information."""
        return self.db

    def store(self, *args, **kwargs):
        """The current implementation of this backend is read-only."""
        raise NotImplementedError("The Questionnaire backend is read-only")

    def save(self, *args, **kwargs):
        """The current implementation of this backend is read-only."""
        raise NotImplementedError("The Questionnaire backend is read-only")

    def delete(self, _id):
        """The current implementation of this backend is read-only."""
        raise NotImplementedError("The Questionnaire backend is read-only")
