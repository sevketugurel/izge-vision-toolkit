import unittest
from dataclasses import replace
from datetime import date, datetime, timezone
from izge.domain import Observation, derive_intervals, fuse, day_bounds, FeedingAccessRule, validate_polygon, union_seconds, subtract_spans
from izge.config import PILOT_CONFIG

class DomainTests(unittest.TestCase):
    def sample(self,t,**kwargs):
        values=dict(offset=t,track_id='1',animal_id='cow',identity='resolved',body_visible=True,
            head_visible=True,feeder_visible=True,posture='standing',activity='feeding',quality=.9,
            origin='model',feed_present=True,confidence=.9)
        return Observation(**(values|kwargs))

    def test_unknown_and_long_gaps_never_extend(self):
        rows=[self.sample(0),self.sample(1),self.sample(2,body_visible=False,head_visible=False),self.sample(9),self.sample(10)]
        intervals=derive_intervals(rows)
        self.assertFalse(any(x['end_offset']>10 for x in intervals))
        self.assertEqual(sum(x['end_offset']-x['start_offset'] for x in intervals if x['dimension']=='activity'),2)
        self.assertFalse([x for x in intervals if x['dimension']=='posture'])

    def test_unresolved_identity_does_not_get_durations(self):
        self.assertEqual(derive_intervals([self.sample(i,identity='unknown') for i in range(10)]),[])

    def test_posture_transition_requires_confirmation(self):
        rows=[self.sample(i,posture='standing' if i<5 else 'lying') for i in range(10)]
        intervals=derive_intervals(rows)
        posture=[x for x in intervals if x['dimension']=='posture']
        self.assertEqual(posture[0]['start_offset'],2)
        self.assertEqual(next(x for x in posture if x['label']=='lying')['start_offset'],7)
        self.assertFalse(any(x['start_offset']==4 for x in posture))

    def test_interval_union(self):
        self.assertEqual(union_seconds([(0,10),(2,12),(13,15)]),14)

    def test_camera_overlap_and_conflict(self):
        a=dict(start=0,end=10,label='standing',quality=.9,camera_id='a',clock_verified=True)
        same=dict(a,camera_id='b')
        self.assertEqual(fuse([a,same],[(0,20)])['measured_seconds'],10)
        conflict=dict(same,label='lying')
        self.assertEqual(fuse([a,conflict],[(0,10)])['unknown_reasons'],{'camera_conflict':10})
        self.assertEqual(fuse([a,dict(same,clock_verified=False)],[(0,10)])['unknown_reasons'],{'clock_unverified':10})
        self.assertEqual(fuse([a,dict(conflict,quality=.5)],[(0,10)])['seconds'],{'standing':10})

    def test_day_boundaries_and_elapsed_time(self):
        now=datetime(2026,10,10,0,30,tzinfo=timezone.utc)
        a,b=day_bounds(date(2026,10,10),'Europe/Istanbul',now)
        self.assertEqual(a,datetime(2026,10,9,21,tzinfo=timezone.utc))
        self.assertEqual((b-a).total_seconds(),12600)
        future=datetime(2026,12,1,tzinfo=timezone.utc)
        a,b=day_bounds(date(2026,3,8),'America/New_York',future)
        self.assertEqual((b-a).total_seconds(),23*3600)
        a,b=day_bounds(date(2026,11,1),'America/New_York',future)
        self.assertEqual((b-a).total_seconds(),25*3600)

    def test_single_approach_no_event(self):
        self.assertEqual(FeedingAccessRule().evaluate([self.sample(0,signal='approach')],PILOT_CONFIG),[])

    def sequences(self,**kwargs):
        return [self.sample(t+shift,signal=signal,**kwargs) for shift in (0,9,18)
                for t,signal in ((1,'approach'),(3,'interference'),(5,'withdrawal'))]

    def test_three_sequences_one_event_unknown_actor_preserved(self):
        events=FeedingAccessRule().evaluate(self.sequences(),PILOT_CONFIG)
        self.assertEqual(len(events),1)
        self.assertTrue(events[0]['sufficient'])
        self.assertEqual(events[0]['details']['sequences'],3)
        self.assertIsNone(events[0]['actor'])

    def test_insufficient_feed_candidate_not_notification(self):
        events=FeedingAccessRule().evaluate(self.sequences(feed_present=None),PILOT_CONFIG)
        self.assertEqual(len(events),1)
        self.assertFalse(events[0]['sufficient'])

    def test_manual_evidence_no_fake_confidence(self):
        events=FeedingAccessRule().evaluate(self.sequences(origin='manual',confidence=None),PILOT_CONFIG)
        self.assertTrue(events[0]['sufficient'])
        self.assertIsNone(events[0]['confidence'])

    def test_sequence_time_limit_and_close(self):
        rows=[self.sample(0,signal='approach'),self.sample(5,signal='interference'),self.sample(31,signal='withdrawal')]
        self.assertEqual(FeedingAccessRule().evaluate(rows,PILOT_CONFIG),[])
        rows=self.sequences()+[replace(o,offset=o.offset+150) for o in self.sequences()]
        self.assertEqual(len(FeedingAccessRule().evaluate(rows,PILOT_CONFIG)),2)

    def test_contact_duration_needs_supported_interference(self):
        sparse=[self.sample(0,signal='approach'),self.sample(1,signal='interference'),self.sample(20,signal='withdrawal')]
        self.assertFalse(FeedingAccessRule().evaluate(sparse,PILOT_CONFIG)[0]['sufficient'])
        dense=[self.sample(0,signal='approach')]+[self.sample(t,signal='interference') for t in range(1,18)]+[self.sample(18,signal='withdrawal')]
        self.assertTrue(FeedingAccessRule().evaluate(dense,PILOT_CONFIG)[0]['sufficient'])

    def test_feed_visibility_is_not_feed_evidence(self):
        rows=[self.sample(i,feed_present=None) for i in range(5)]
        self.assertFalse([x for x in derive_intervals(rows) if x['dimension']=='activity'])

    def test_new_analysis_replaces_only_its_range(self):
        self.assertEqual(subtract_spans((0,30),[(10,20)]),[(0,10),(20,30)])
        self.assertEqual(subtract_spans((0,30),[(0,10),(10,30)]),[])

    def test_notification_cooldown_independent_of_event_close(self):
        config=PILOT_CONFIG|{'repeat_count':1,'close_silence':1}
        events=FeedingAccessRule().evaluate(self.sequences(),config)
        self.assertEqual(len(events),3)
        self.assertEqual(sum(e['details']['notify'] for e in events),1)

    def test_uncertain_actor_not_assigned(self):
        events=FeedingAccessRule().evaluate(self.sequences(actor_id='maybe',actor_identity='unknown'),PILOT_CONFIG)
        self.assertIsNone(events[0]['actor'])

    def test_polygons(self):
        validate_polygon([[0,0],[1,0],[1,1],[0,1]])
        for invalid in ([[0,0],[1,1],[0,1],[1,0]],[[0,0],[1,1],[2,0]],[[0,0],[0,0],[1,1]],[[0,0],[.5,.5],[1,1]]):
            with self.assertRaises(ValueError):
                validate_polygon(invalid)

if __name__=='__main__':
    unittest.main()
