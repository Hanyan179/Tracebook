import datetime as dt
import http.client
import json
import threading
import unittest
import test_node as fixtures
from store import Store, StoreError
from server import Server


class DailySettingsTests(unittest.TestCase):
    setUp = fixtures.NodeTests.setUp
    tearDown = fixtures.NodeTests.tearDown
    receive = fixtures.NodeTests.receive
    fixture = fixtures.NodeTests.fixture
    def test_schedule_waits_until_cutoff_and_catches_up(self):
        self.receive('2026-10-18T04:00:00Z')
        self.receive('2026-10-19T04:00:00Z')
        self.store.configure_daily({'enabled': True, 'time': '08:15', 'timeZone': 'Asia/Shanghai'})
        self.store.catch_up(dt.datetime.fromisoformat('2026-10-20T00:14:00+00:00'))
        self.assertTrue((self.store.output/'daily/2026-10-18.md').exists())
        self.assertFalse((self.store.output/'daily/2026-10-19.md').exists())
        self.store.catch_up(dt.datetime.fromisoformat('2026-10-20T00:15:00+00:00'))
        self.assertTrue((self.store.output/'daily/2026-10-19.md').exists())
        current=self.store.daily_settings(dt.datetime.fromisoformat('2026-10-20T00:16:00+00:00'))
        self.assertEqual(current['nextRun'],'2026-10-21T08:15:00+08:00')
        self.assertIsNotNone(current['lastRun'])

    def test_disabled_schedule_survives_restart_and_manual_generation_still_works(self):
        self.receive()
        self.store.configure_daily({'enabled': False, 'time': '09:30', 'timeZone': 'UTC'})
        self.store.close();self.store=Store(self.root/'node')
        self.store.catch_up(dt.datetime.fromisoformat('2026-10-21T12:00:00+00:00'))
        self.assertFalse((self.store.output/'daily/2026-10-19.md').exists())
        self.assertIsNone(self.store.daily_settings()['nextRun'])
        self.assertEqual(self.store.daily_settings()['time'],'09:30')
        self.store.build_daily('2026-10-19')
        self.assertTrue((self.store.output/'daily/2026-10-19.md').exists())

    def test_invalid_schedule_leaves_existing_settings_intact(self):
        before=self.store.daily_settings()
        for changes in [{'time':'24:99'},{'enabled':'yes'},{'timeZone':'Not/AZone'}]:
            with self.assertRaises(StoreError):
                self.store.configure_daily({'enabled':True,'time':'08:30','timeZone':'UTC',**changes})
            after=self.store.daily_settings()
            self.assertEqual((after['time'],after['enabled'],after['timeZone']), (before['time'],before['enabled'],before['timeZone']))

    def test_daily_settings_http_requires_authentication_and_persists(self):
        server=Server(('127.0.0.1',0),self.store,'schedule-test-only')
        worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
        try:
            connection=http.client.HTTPConnection(*server.server_address)
            connection.request('GET','/v1/daily-settings')
            response=connection.getresponse();self.assertEqual(response.status,401);response.read();connection.close()
            connection=http.client.HTTPConnection(*server.server_address)
            body={'enabled':False,'time':'10:45','timeZone':'UTC'}
            headers={'Authorization':'Bearer '+server.token,'Content-Type':'application/json'}
            connection.request('PUT','/v1/daily-settings',json.dumps(body),headers)
            response=connection.getresponse();self.assertEqual(response.status,200)
            self.assertEqual(json.loads(response.read())['time'],'10:45');connection.close()
        finally:server.shutdown();server.server_close();worker.join()
