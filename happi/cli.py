"""
This module defines the ``happi`` command line utility
"""
# cli.py

import argparse
import fnmatch
import logging
import os
import sys

import coloredlogs
from IPython import start_ipython
from .utils import is_a_range

import happi

from happi.audit import Audit
audit = Audit()

logger = logging.getLogger(__name__)


def get_parser():
    """
    Defines HAPPI shell commands
    """
    # Argument Parser Setup
    parser = argparse.ArgumentParser(description='happi command line tool')

    # Optional args general to all happi operations
    parser.add_argument('--path', type=str,
                        help='path to happi configuration file')
    parser.add_argument('--verbose', '-v', action='store_true',
                        help='Show the degub logging stream')
    parser.add_argument('--version', '-V', action='store_true',
                        help='Current version and location '
                        'of Happi installation.')
    # Subparser to trigger search arguments
    subparsers = parser.add_subparsers(help='Subparsers to search, add, edit',
                                       dest='cmd')
    parser_search = subparsers.add_parser('search', help='Search the happi '
                                          'database')
    parser_search.add_argument('search_criteria', nargs='+',
                               help='Search criteria: '
                               'field=value. If field= is '
                               'omitted, it will be assumed to be "name". '
                               'You may include as many search criteria as '
                               'you like.')
    parser_add = subparsers.add_parser('add', help='Add new entries')
    parser_add.add_argument('--clone', default='',
                            help='Name of device to use for default parameters'
                            )
    parser_edit = subparsers.add_parser('edit', help='Change existing entry')
    parser_edit.add_argument('name', help='Device to edit')
    parser_edit.add_argument('edits', nargs='+',
                             help='Edits of the form field=value')
    parser_load = subparsers.add_parser('load',
                                        help='Open IPython terminal with '
                                        'device loaded')
    parser_load.add_argument('device_names', nargs='+',
                             help='Devices to load')
    parser_audit = subparsers.add_parser(audit.name, help=audit.help)
    audit.add_args(parser_audit)

    return parser


def happi_cli(args):
    parser = get_parser()
    # print happi usage if no arguments are provided
    if not args:
        parser.print_usage()
        return
    args = parser.parse_args(args)

    # Logging Level handling
    if args.verbose:
        shown_logger = logging.getLogger()
        level = "DEBUG"
    else:
        shown_logger = logging.getLogger('happi')
        level = "INFO"
    coloredlogs.install(level=level, logger=shown_logger,
                        fmt='[%(asctime)s] - %(levelname)s -  %(message)s')
    logger.debug("Set logging level of %r to %r", shown_logger.name, level)

    # Version endpoint
    if args.version:
        print(f'Happi: Version {happi.__version__} from {happi.__file__}')
        return
    logger.debug('Command line arguments: %r' % args)

    client = happi.client.Client.from_config(cfg=args.path)
    logger.debug("Happi client: %r" % client)
    logger.debug('Happi command: %r' % args.cmd)

    if args.cmd == 'search':
        logger.debug("We're in the search block")

        # Get search criteria into dictionary for use by client
        client_args = {}
        range_list = []
        regex_list = []
        is_range = False
        num_args = len(args.search_criteria)
        for user_arg in args.search_criteria:
            if '=' in user_arg:
                criteria, value = user_arg.split('=', 1)
            else:
                criteria = 'name'
                value = user_arg
            if criteria in client_args:
                logger.error(
                    'Received duplicate search criteria %s=%r (was %r)',
                    criteria, value, client_args[criteria]
                )
                return
            if value.replace('.', '').isnumeric():
                logger.debug('Changed %s to float', value)
                value = str(float(value))

            if is_a_range(value):
                start, stop = value.split(',')
                start = float(start)
                stop = float(stop)
                is_range = True
                if start < stop:
                    range_list = client.search_range(criteria, start, stop)
                else:
                    logger.error('Invalid range, make sure start < stop')

            # skip the criteria for range values
            # it won't be a valid criteria for search_regex()
            if is_a_range(str(value)):
                pass
            else:
                client_args[criteria] = fnmatch.translate(value)

        regex_list = client.search_regex(**client_args)
        results = regex_list + range_list

        # find the repeated items
        res_size = len(results)
        repeated = []
        for i in range(res_size):
            k = i + 1
            for j in range(k, res_size):
                if results[i] == results[j] and results[i] not in repeated:
                    repeated.append(results[i])

        # if we search both range and regex but
        # they don't have a common item just return
        if num_args > 1 and is_range and not repeated:
            logger.error('No devices found')
            return
        # we only want to return the ones that have been repeated when
        # they have been matched with both search_regex() & search_range()
        elif repeated:
            for res in repeated:
                res.item.show_info()
            return repeated
        # only matched with search_regex()
        elif regex_list and not is_range:
            for res in regex_list:
                res.item.show_info()
            return regex_list
        # only matched with search_range()
        elif range_list and is_range:
            for res in range_list:
                res.item.show_info()
            return range_list
        else:
            logger.error('No devices found')
    elif args.cmd == 'add':
        logger.debug('Starting interactive add')
        registry = happi.containers.registry
        if args.clone:
            clone_source = client.find_device(name=args.clone)
            # Must use the same container if cloning
            response = registry.entry_for_class(clone_source.__class__)
        else:
            # Keep Device at registry for backwards compatibility but filter
            # it out of new devices options
            options = os.linesep.join(
                [k for k, _ in registry.items() if k != "Device"]
            )
            logger.info(
                'Please select a container, or press enter for generic '
                'Ophyd Device container: %s%s', os.linesep, options
            )
            response = input()
            if response and response not in registry:
                logger.info('Invalid device container %s', response)
                return
            elif not response:
                response = 'OphydItem'

        container = registry[response]
        kwargs = {}
        for info in container.entry_info:
            valid_value = False
            while not valid_value:
                if args.clone:
                    default = getattr(clone_source, info.key)
                else:
                    default = info.default
                logger.info(f'Enter value for {info.key}, default={default}, '
                            f'enforce={info.enforce}')
                item_value = input()
                if not item_value:
                    if info.optional or args.clone:
                        logger.info(f'Selecting default value {default}')
                        item_value = default
                    else:
                        logger.info('Not an optional field!')
                        continue
                try:
                    info.enforce_value(item_value)
                    valid_value = True
                    kwargs[info.key] = item_value
                except Exception as e:
                    logger.info('Invalid value %s, %s', item_value, e)

        device = client.create_device(container, **kwargs)
        logger.info('Please confirm the following info is correct:')
        device.show_info()
        ok = input('y/N\n')
        if 'y' in ok:
            logger.info('Adding device')
            device.save()
        else:
            logger.info('Aborting')
    elif args.cmd == 'edit':
        logger.debug('Starting edit block')
        device = client.find_device(name=args.name)
        is_invalid_field = False
        for edit in args.edits:
            field, value = edit.split('=', 1)
            try:
                getattr(device, field)
                logger.info('Setting %s.%s = %s', args.name, field, value)
                setattr(device, field, value)
            except Exception as e:
                is_invalid_field = True
                logger.error('Could not edit %s.%s: %s', args.name, field, e)
        if is_invalid_field:
            sys.exit(1)
        device.save()
        device.show_info()
    elif args.cmd == 'load':
        logger.debug('Starting load block')
        logger.info(f'Creating shell with devices {args.device_names}')
        devices = {}
        for name in args.device_names:
            devices[name] = client.load_device(name=name)
        start_ipython(argv=['--quick'], user_ns=devices)
    elif args.cmd == 'audit':
        audit.run(args)


def main():
    """Execute the ``happi_cli`` with command line arguments"""
    happi_cli(sys.argv[1:])
