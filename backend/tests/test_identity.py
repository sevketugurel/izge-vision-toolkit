import unittest
from datetime import datetime, timezone
from izge.adapters import SnapshotIdentityResolver

class IdentityTests(unittest.TestCase):
    def context(self):
        return {'recorded_at':'2026-01-01T00:00:00+00:00','geometry_valid':True,
                'zones':[{'stall_id':'stall','role':'body'}],
                'occupancies':[
                    {'animal_id':'old','stall_id':'stall','start':'2025-01-01T00:00:00+00:00','end':'2026-01-01T00:00:10+00:00'},
                    {'animal_id':'new','stall_id':'stall','start':'2026-01-01T00:00:10+00:00','end':None}]}
    def test_historical_identity_half_open_boundary(self):
        resolver=SnapshotIdentityResolver(self.context())
        self.assertEqual(resolver.at_offset(9),[('old','stall')])
        self.assertEqual(resolver.at_offset(10),[('new','stall')])
    def test_unverified_recording_never_uses_current_occupancy(self):
        ctx=self.context()|{'recorded_at':None}
        self.assertEqual(SnapshotIdentityResolver(ctx).at_offset(999),[])
    def test_head_zone_alone_cannot_assign_ownership(self):
        ctx=self.context()|{'zones':[{'stall_id':'stall','role':'head'}]}
        self.assertEqual(SnapshotIdentityResolver(ctx).at_offset(1),[])
    def test_moved_camera_requires_geometry_confirmation(self):
        ctx=self.context()|{'geometry_valid':False}
        self.assertEqual(SnapshotIdentityResolver(ctx).at_offset(1),[])
    def test_ambiguous_history_does_not_force_identity(self):
        ctx=self.context()
        ctx['occupancies'].append(ctx['occupancies'][0]|{'animal_id':'ambiguous'})
        self.assertIsNone(SnapshotIdentityResolver(ctx).resolve('stall',datetime(2026,1,1,tzinfo=timezone.utc)))

if __name__=='__main__':
    unittest.main()
