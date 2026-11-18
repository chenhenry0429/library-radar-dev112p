import csv,json,tempfile,unittest
from pathlib import Path
from label_tools import FIELDS,build,read_labels,validate_split,save
from evaluate_presence import evaluate


class EvaluationTests(unittest.TestCase):
    def setUp(self):
        base=Path(__file__).parent/"test-results";base.mkdir(exist_ok=True)
        self.temp=tempfile.TemporaryDirectory(dir=base);self.root=Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)
        self.meta=self.root/"meta.json";self.algorithm=self.root/"algorithm.json";self.annotation=self.root/"annotation.json"
        save(self.meta,dict(recording_id="fixture",seat_id="T01",source_kind="synthetic"))
        save(self.algorithm,dict(recording_id="fixture",algorithm_version="test",parameter_version="fixed"))
        save(self.annotation,dict(recording_id="fixture",source_kind="synthetic",alignment_confirmed=True,status="reviewed",timing_offset_s=0))
        self.labels=self.root/"labels.csv";self.result=self.root/"result.csv"

    def labels_write(self,rows):
        with self.labels.open('w',encoding='utf-8',newline='') as f:
            w=csv.DictWriter(f,fieldnames=FIELDS);w.writeheader()
            for start,end,p in rows:w.writerow(dict(start_timestamp=start,end_timestamp=end,seat_id="T01",presence_label=p,posture_label="not_applicable" if p=='0' else "unknown",event_label="synthetic_fixture",annotator="test"))

    def predictions(self,rows):
        with self.result.open('w',encoding='utf-8',newline='') as f:
            w=csv.writer(f);w.writerow(['frame_id','timestamp','presence','quality'])
            for i,(t,p) in enumerate(rows):w.writerow([i,t,p,'ok'])

    def score(self,**kw):
        return evaluate(self.result,self.labels,self.annotation,self.meta,self.algorithm,2,**kw)

    def test_half_open_boundary(self):
        self.labels_write([(0,2,'0'),(2,4,'1')]);self.predictions([(0,0),(1,0),(2,1),(3,1)])
        r=self.score();self.assertEqual(r['frame_confusion']['tn'],2);self.assertEqual(r['frame_confusion']['tp'],2)

    def test_rejection_not_hidden(self):
        self.labels_write([(0,2,'0'),(2,4,'1')]);self.predictions([(0,-1),(1,1),(2,0),(3,-1)])
        r=self.score();self.assertEqual(r['coverage'],.5);self.assertEqual(r['frame_confusion']['unknown_empty'],1);self.assertEqual(r['occupancy_not_detected_fraction'],1)

    def test_unknown_labels_excluded(self):
        self.labels_write([(0,1,'unknown'),(1,3,'0')]);self.predictions([(0,1),(1,0),(2,0)])
        self.assertEqual(self.score()['excluded_unknown_label_frames'],1)

    def test_gap_never_filled(self):
        self.labels_write([(0,20,'1')]);self.predictions([(0,1),(1,1),(10,1)])
        r=self.score();self.assertEqual(r['excluded_gap_s'],9);self.assertEqual(sum(r['time_confusion_s'].values()),1)

    def test_no_tail_extrapolation(self):
        self.labels_write([(0,100,'0')]);self.predictions([(0,0),(1,0)])
        self.assertEqual(sum(self.score()['time_confusion_s'].values()),1)

    def test_overlap_rejected(self):
        self.labels_write([(0,3,'0'),(2,4,'1')])
        with self.assertRaisesRegex(ValueError,'重叠'):read_labels(self.labels)

    def test_split_leakage_rejected(self):
        with self.assertRaisesRegex(ValueError,'同时'):validate_split(dict(development=['P02'],test=['P02']))

    def test_alignment_requires_opt_in_and_offset(self):
        self.labels_write([(0,3,'0')]);self.predictions([(0,0),(1,0)])
        save(self.annotation,dict(recording_id="fixture",source_kind="synthetic",alignment_confirmed=False,status="pending",timing_offset_s=None))
        with self.assertRaisesRegex(ValueError,'待确认'):self.score()
        with self.assertRaisesRegex(ValueError,'offset'):self.score(allow_provisional=True)
        self.assertEqual(self.score(offset_s=0,allow_provisional=True)['status'],'provisional_not_acceptance')

    def test_measured_synthetic_cannot_mix(self):
        self.labels_write([(0,2,'0')]);self.predictions([(0,0),(1,0)])
        save(self.annotation,dict(recording_id="fixture",source_kind="measured"))
        with self.assertRaisesRegex(ValueError,'混合'):self.score()

    def test_offset_direction(self):
        self.labels_write([(0,1,'0'),(1,2,'1')]);self.predictions([(2,0),(3,1)])
        r=self.score(offset_s=2);self.assertEqual(r['frame_confusion']['tn'],1);self.assertEqual(r['frame_confusion']['tp'],1)

    def test_missing_class_metric_is_null(self):
        self.labels_write([(0,3,'0')]);self.predictions([(0,0),(1,0)])
        self.assertIsNone(self.score()['false_negative_fraction_all_occupied'])
        self.assertIsNone(self.score()['event_latency_s'])

    def test_wrong_recording_length_rejected(self):
        self.labels_write([(0,3,'0')]);self.predictions([(0,0),(1,0)])
        save(self.meta,dict(recording_id="fixture",seat_id="T01",source_kind="synthetic",raw_shape=[99,8]))
        with self.assertRaisesRegex(ValueError,'行数'):self.score()

    def test_generated_labels_do_not_invent_initial_empty_or_posture(self):
        meta=dict(recording_id="field_fixture",seat_id="T01",subject_id="PTEST",source_kind="measured",
                  raw_shape=[101,8],radar_config=dict(nominal_frame_period_s=1),
                  annotation=dict(stopwatch_events=[dict(time_s=10,event="坐稳"),dict(time_s=70,event="离座")]))
        save(self.meta,meta);record=self.root/'record.md';record.write_text('独立现场记录',encoding='utf-8')
        dest=build(self.meta,record,self.root/'labels')
        rows=read_labels(dest/'labels.csv')
        self.assertEqual(rows[0]['presence_label'],'unknown')
        self.assertEqual(rows[1]['start_timestamp'],12)
        self.assertEqual(rows[1]['end_timestamp'],65)
        self.assertTrue(all(r['posture_label'] in ('unknown','not_applicable') for r in rows))
        self.assertFalse(json.loads((dest/'annotation.json').read_text(encoding='utf-8'))['alignment_confirmed'])

    def test_empty_target_requires_observer_statement(self):
        save(self.meta,dict(recording_id="field_fixture",seat_id="T01",subject_id=None,source_kind="measured",
             raw_shape=[10,8],radar_config=dict(nominal_frame_period_s=1)))
        record=self.root/'record.md';record.write_text('只记录邻座有人，未确认目标座',encoding='utf-8')
        with self.assertRaisesRegex(ValueError,'明确声明'):build(self.meta,record,self.root/'labels',True)


if __name__=='__main__':unittest.main()
