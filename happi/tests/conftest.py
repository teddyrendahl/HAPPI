import logging
from unittest.mock import patch

import pytest
import simplejson

from happi import Client, OphydItem
from happi.backends.json_db import JSONBackend

logger = logging.getLogger(__name__)

# Conditional import of pymongo, mongomock
try:
    from happi.backends.mongo_db import MongoBackend
    from mongomock import MongoClient
    supported_backends = ['json', 'mongo']
except ImportError as exc:
    logger.warning('Error importing mongomock : -> %s', exc)
    supported_backends = ['json']

requires_mongo = pytest.mark.skipif('mongo' not in supported_backends,
                                    reason='Missing mongo')

# Conditional import of psdm_qs_cli
try:
    from psdm_qs_cli import QuestionnaireClient
    from happi.backends.qs_db import QSBackend
    has_qs_cli = True
except ImportError as exc:
    logger.warning('Error importing psdm_qs_cli : -> %s', exc)
    has_qs_cli = False


requires_questionnaire = pytest.mark.skipif(not has_qs_cli,
                                            reason='Missing psdm_qs_cli')


try:
    import pcdsdevices  # noqa
    has_pcdsdevices = True
except ImportError as exc:
    logger.warning('error importing pcdsdevices : -> %s', exc)
    has_pcdsdevices = False


requires_pcdsdevices = pytest.mark.skipif(not has_pcdsdevices,
                                          reason='Missing pcdsdevices')


@pytest.fixture(scope='function')
def device_info():
    return {'name': 'alias',
            'z': 400,
            '_id': 'alias',
            'prefix': 'BASE:PV',
            'beamline': 'LCLS',
            'type': 'OphydItem',
            'device_class': 'types.SimpleNamespace',
            'args': list(),
            'kwargs': {'hi': 'oh hello'},
            'location_group': 'LOC',
            'functional_group': 'FUNC',
            }


@pytest.fixture(scope='function')
def device(device_info):
    t = OphydItem(**device_info)
    return t


@pytest.fixture(scope='function')
def valve_info():
    return {'name': 'name',
            'z': 300,
            'prefix': 'BASE:VGC:PV',
            '_id': 'name',
            'beamline': 'LCLS',
            'mps': 'MPS:VGC:PV',
            'location_group': 'LOC',
            'functional_group': 'FUNC',
            'device_class': 'types.SimpleNamespace'
            }


@pytest.fixture(scope='function')
def valve(valve_info):
    t = OphydItem(**valve_info)
    return t


@pytest.fixture(scope='function')
def mockjsonclient(device_info):
    # Write underlying database
    with open('testing.json', 'w+') as handle:
        simplejson.dump({device_info['name']: device_info},
                        handle)
    # Return handle name
    db = JSONBackend('testing.json')
    return Client(database=db)


@pytest.fixture(scope='function')
@requires_mongo
def mockmongoclient(device_info):
    with patch('happi.backends.mongo_db.MongoClient') as mock_mongo:
        mc = MongoClient()
        mc['test_db'].create_collection('test_collect')
        mock_mongo.return_value = mc
        # Client
        backend = MongoBackend(db='test_db',
                               collection='test_collect')
        client = Client(database=backend)
        # Insert a single device
        client.backend._collection.insert_one(device_info)
        return client


if 'mongo' in supported_backends:
    @pytest.fixture(scope='function', params=supported_backends)
    def happi_client(request, mockmongoclient, mockjsonclient):
        if request.param == 'json':
            return mockjsonclient
        if request.param == 'mongo':
            return mockmongoclient
else:
    @pytest.fixture(scope='function', params=supported_backends)
    def happi_client(request, mockjsonclient):
        return mockjsonclient


@pytest.fixture(scope='module')
def mockqsbackend():
    # Create a very basic mock class
    class MockQuestionnaireClient(QuestionnaireClient):

        def getProposalsListForRun(self, run):
            return {'X534': {'Instrument': 'TST', 'proposal_id': 'X534'},
                    'LR32': {'Instrument': 'TST', 'proposal_id': 'LR32'},
                    'LU34': {'Instrument': 'MFX', 'proposal_id': 'LU34'}}

        def getProposalDetailsForRun(self, run_no, proposal):
            return {
                'pcdssetup-motors-1-location': 'Hutch-main experimental',
                'pcdssetup-motors-1-name': 'sam_x',
                'pcdssetup-motors-1-purpose': 'sample x motion',
                'pcdssetup-motors-1-pvbase': 'TST:USR:MMS:01',
                'pcdssetup-motors-1-stageidentity': 'IMS MD23',
                'pcdssetup-motors-2-location': 'Hutch-main experimental',
                'pcdssetup-motors-2-name': 'sam_z',
                'pcdssetup-motors-2-purpose': 'sample z motion',
                'pcdssetup-motors-2-pvbase': 'TST:USR:MMS:02',
                'pcdssetup-motors-2-stageidentity': 'IMS MD23',
                'pcdssetup-motors-3-location': 'Hutch-main experimental',
                'pcdssetup-motors-3-name': 'sam_y',
                'pcdssetup-motors-3-purpose': 'sample y motion',
                'pcdssetup-motors-3-pvbase': 'TST:USR:MMS:03',
                'pcdssetup-motors-3-stageidentity': 'IMS MD32',
                'pcdssetup-motors-4-location': 'Hutch-main experimental',
                'pcdssetup-motors-4-name': 'sam_r',
                'pcdssetup-motors-4-purpose': 'sample rotation',
                'pcdssetup-motors-4-pvbase': 'TST:USR:MMS:04',
                'pcdssetup-motors-4-stageidentity': 'IMS MD23',
                'pcdssetup-motors-5-location': 'Hutch-main experimental',
                'pcdssetup-motors-5-name': 'sam_az',
                'pcdssetup-motors-5-purpose': 'sample azimuth',
                'pcdssetup-motors-5-pvbase': 'TST:USR:MMS:05',
                'pcdssetup-motors-5-stageidentity': 'IMS MD23',
                'pcdssetup-motors-6-location': 'Hutch-main experimental',
                'pcdssetup-motors-6-name': 'sam_flip',
                'pcdssetup-motors-6-purpose': 'sample flip',
                'pcdssetup-motors-6-pvbase': 'TST:USR:MMS:06',
                'pcdssetup-motors-6-stageidentity': 'IMS MD23',
                'pcdssetup-trig-1-delay': '0.00089',
                'pcdssetup-trig-1-eventcode': '198',
                'pcdssetup-trig-1-name': 'Overview_trig',
                'pcdssetup-trig-1-polarity': 'positive',
                'pcdssetup-trig-1-purpose': 'Overview',
                'pcdssetup-trig-1-pvbase': 'MFX:REC:EVR:02:TRIG1',
                'pcdssetup-trig-1-width': '0.00075',
                'pcdssetup-trig-2-delay': '0.000894348',
                'pcdssetup-trig-2-eventcode': '198',
                'pcdssetup-trig-2-name': 'Meniscus_trig',
                'pcdssetup-trig-2-polarity': 'positive',
                'pcdssetup-trig-2-purpose': 'Meniscus',
                'pcdssetup-trig-2-pvbase': 'MFX:REC:EVR:02:TRIG3',
                'pcdssetup-trig-2-width': '0.0005',
                'pcdssetup-ao-1-device': 'Acromag IP231 16-bit',
                'pcdssetup-ao-1-name': 'irLed',
                'pcdssetup-ao-1-purpose': 'IR LED',
                'pcdssetup-ao-2-channel': '6',
                'pcdssetup-ao-2-device': 'Acromag IP231 16-bit',
                'pcdssetup-ao-2-name': 'laser_shutter_opo',
                'pcdssetup-ao-2-purpose': 'OPO Shutter',
                'pcdssetup-ao-3-channel': '7',
                'pcdssetup-ao-3-device': 'Acromag IP231 16-bit',
                'pcdssetup-ao-3-name': 'laser_shutter_evo1',
                'pcdssetup-ao-3-purpose': 'EVO Shutter1',
                'pcdssetup-ao-4-channel': '2',
                'pcdssetup-ao-4-device': 'Acromag IP231 16-bit',
                'pcdssetup-ao-4-name': 'laser_shutter_evo2',
                'pcdssetup-ao-4-purpose': 'EVO Shutter2',
                'pcdssetup-ao-5-channel': '3',
                'pcdssetup-ao-5-device': 'Acromag IP231 16-bit',
                'pcdssetup-ao-5-name': 'laser_shutter_evo3',
                'pcdssetup-ao-5-purpose': 'EVO Shutter3',
                'pcdssetup-ao-1-pvbase': 'MFX:USR:ao1:0',
                'pcdssetup-ao-2-pvbase': 'MFX:USR:ao1:6',
                'pcdssetup-ao-3-pvbase': 'MFX:USR:ao1:7',
                'pcdssetup-ao-4-pvbase': 'MFX:USR:ao1:2',
                'pcdssetup-ao-5-pvbase': 'MFX:USR:ao1:3',
                'pcdssetup-ai-1-device': 'Acromag IP231 16-bit',
                'pcdssetup-ai-1-name': 'irLed',
                'pcdssetup-ai-1-purpose': 'IR LED',
                'pcdssetup-ai-1-pvbase': 'MFX:USR:ai1:0'}

        def getExpName2URAWIProposalIDs(self):
            return {
                'tstx53416': 'X534',
                'tstlr3216': 'LR32',
                'mfxlu3417': 'LU34'}

    with patch('happi.backends.qs_db.QuestionnaireClient') as qs_cli:
        # Replace QuestionnaireClient with our test version
        mock_qs = MockQuestionnaireClient(use_kerberos=False,
                                          user='user', pw='pw')
        qs_cli.return_value = mock_qs
        # Instantiate a fake device
        backend = QSBackend('tstlr3216')
        return backend


@pytest.fixture(scope='function')
def three_valves(happi_client):
    valve1 = {'name': 'valve1',
              'z': 300,
              'prefix': 'BASE:VGC1:PV',
              '_id': 'VALVE1',
              'beamline': 'LCLS',
              'mps': 'MPS:VGC:PV',
              'type': 'OphydItem',
              'location_group': 'LOC',
              'functional_group': 'FUNC',
              'device_class': 'types.SimpleNamespace',
              'args': list(),
              'kwargs': {'hi': 'oh hello'},
              }

    valve2 = {'name': 'valve2',
              'z': 301,
              'prefix': 'BASE:VGC2:PV',
              '_id': 'VALVE2',
              'beamline': 'LCLS',
              'mps': 'MPS:VGC:PV',
              'type': 'OphydItem',
              'location_group': 'LOC',
              'functional_group': 'FUNC',
              'device_class': 'types.SimpleNamespace',
              'args': list(),
              'kwargs': {'hi': 'oh hello'},
              }

    valve3 = {'name': 'valve3',
              'z': 301,
              'prefix': 'BASE:VGC3:PV',
              '_id': 'VALVE3',
              'beamline': 'LCLS',
              'mps': 'MPS:VGC:PV',
              'location_group': 'LOC',
              'functional_group': 'FUNC',
              'type': 'OphydItem',
              'device_class': 'types.SimpleNamespace',
              'args': list(),
              'kwargs': {'hi': 'oh hello'},
              }

    for dev in happi_client.all_devices:
        happi_client.backend.delete(dev['_id'])

    valves = dict(
        VALVE1=valve1,
        VALVE2=valve2,
        VALVE3=valve3,
    )

    for name, valve in valves.items():
        happi_client.backend.save(name, valve, insert=True)
    return valves
