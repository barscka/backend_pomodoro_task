import json
import os
from datetime import date, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from django.test import override_settings
from rest_framework.renderers import JSONRenderer
from rest_framework.test import APITestCase

from . import test_spec_017 as fixtures
from .models import (
    Activity, Category, GameplayTrackingSettings, GoalCompletion, Group, History,
    RetroGame, RetroPlatform, RoutinePlan, RoutineSessionAssociation, Schedule,
)
from .services.activity_execution import complete_schedule, start_activity
from .services.premium_execution import start_premium
from .services.retro_execution import start_retro
from .services.routine_reporting import summary
from .services.routine_sessions import agenda
from .services import routines
from .serializers import ActivityExecutionSerializer


@override_settings(PREMIUM_DIRECT_START_ENABLED=True, RETROGAMES_ENABLED=True)
class RoutineSessionTests(APITestCase):
    setUp = fixtures.RoutineTests.setUp
    at = fixtures.RoutineTests.at
    template = fixtures.RoutineTests.template
    version = fixtures.RoutineTests.version
    row = fixtures.RoutineTests.row
    period = fixtures.RoutineTests.period
    queue = fixtures.RoutineTests.queue
    choose = fixtures.RoutineTests.choose
    preview = fixtures.RoutineTests.preview
    start_body = fixtures.RoutineTests.start_body

    def configure_gameplay(self):
        config = GameplayTrackingSettings.objects.create(scope_key=self.scope)
        config.groups.add(self.group)

    def premium(self, minutes=60, request_id=None):
        self.p = getattr(self, 'p', None) or self.period()
        return start_premium(period_id=self.p.id, scope_key=self.scope, duration_minutes=minutes, request_id=request_id or uuid4())

    def retro(self, minutes=60, request_id=None):
        if not hasattr(self, 'game'):
            group, _ = Group.objects.get_or_create(is_retro_catalog=True, defaults={'name': 'Retrogames'})
            gen = Category.objects.create(group=group, name='Geração de teste')
            platform = RetroPlatform.objects.create(generation=gen, name='Console', slug='console', sort_order=1)
            activity = Activity.objects.create(category=gen, name='Jogo Retro')
            self.game = RetroGame.objects.create(activity=activity, platform=platform, tier='essential', estimated_main_minutes=120, sort_order=1)
        return start_retro(retro_game_id=self.game.id, scope_key=self.scope, duration_minutes=minutes, request_id=request_id or uuid4())

    def assert_association(self, schedule, origin='2026-10-10'):
        association = RoutineSessionAssociation.objects.get(schedule=schedule)
        self.assertEqual(str(association.origin_date), origin)
        self.assertEqual(association.session_starts_at, schedule.starts_at)
        self.assertEqual(association.execution_origin, schedule.execution_origin)
        return association

    def test_saturday_2337_premium_without_client_occurrence_and_recovery(self):
        self.template()
        self.at(10, 23, 37)
        p = self.period()
        response = self.client.post(f'/api/premium-periods/{p.id}/start/', {'duration_minutes': 120, 'request_id': str(uuid4())}, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        link = response.data['routine_occurrence']
        self.assertEqual(link['origin_date'], '2026-10-10')
        self.assertIsNone(response.data['routine_requested_occurrence'])
        recovered = self.client.get(f"/api/activity-executions/{response.data['execution_id']}/")
        self.assertEqual(recovered.status_code, 200, recovered.data)
        self.assertEqual(recovered.data['routine_occurrence'], link)

    def test_sunday_0015_premium_belongs_to_saturday(self):
        self.template()
        self.at(11, 0, 15)
        session, _ = self.premium()
        self.assert_association(session)
        value = agenda(self.scope, date(2026, 10, 11), date(2026, 10, 11))
        record = value['recorded_occurrences'][0]
        self.assertEqual(record['origin_date'], '2026-10-10')
        self.assertEqual(record['sessions'][0]['execution_id'], session.id)

    def test_queue_at_both_night_times_and_retry_never_reassociates(self):
        self.template()
        self.configure_gameplay()
        self.at(10, 23, 37)
        _, item = self.queue()
        first, _ = start_activity(activity=self.activity, queue_item=item, scope_key=self.scope)
        link = self.assert_association(first)
        self.at(11, 0, 15)
        replay, created = start_activity(activity=self.activity, queue_item=item, scope_key=self.scope)
        self.assertFalse(created)
        self.assertEqual(replay.id, first.id)
        link.refresh_from_db()
        self.assertEqual(link.session_starts_at.astimezone(routines.ZONE).hour, 23)
        complete_schedule(first)
        _, new_item = self.queue()
        session, _ = start_activity(activity=self.activity, queue_item=new_item, scope_key=self.scope)
        self.assert_association(session)
        self.assertEqual(RoutineSessionAssociation.objects.count(), 2)

    def test_retro_at_both_times_and_idempotent_replay(self):
        self.template()
        self.at(10, 23, 37)
        request_id = uuid4()
        first, _ = self.retro(request_id=request_id)
        self.assert_association(first)
        self.at(11, 0, 15)
        replay, created = self.retro(request_id=request_id)
        self.assertFalse(created)
        self.assertEqual(replay.id, first.id)
        self.assertEqual(RoutineSessionAssociation.objects.count(), 1)
        complete_schedule(first)
        second, _ = self.retro()
        self.assert_association(second)

    def test_exact_end_exclusive_and_free_family_or_no_plan_never_link(self):
        self.template()
        for day, hour, minute in [(11, 1, 0), (11, 12, 15), (11, 20, 0)]:
            with self.subTest(time=(day, hour, minute)):
                self.at(day, hour, minute)
                session, _ = self.premium(minutes=1)
                self.assertFalse(RoutineSessionAssociation.objects.filter(schedule=session).exists())
                complete_schedule(session)
        RoutinePlan.objects.all().delete()
        self.at(11, 23, 37)
        session, _ = self.premium()
        self.assertFalse(RoutineSessionAssociation.objects.filter(schedule=session).exists())

    def test_session_overrun_preserves_link_and_civil_summary_counts_once(self):
        self.template()
        self.at(10, 23, 37)
        session, _ = self.premium(minutes=120)
        link = self.assert_association(session)
        self.at(11, 1, 37)
        complete_schedule(session)
        link.refresh_from_db()
        self.assertEqual(link.occurrence_ends_at.astimezone(routines.ZONE).hour, 1)
        value = agenda(self.scope, date(2026, 10, 10), date(2026, 10, 11))
        record = value['recorded_occurrences'][0]
        self.assertEqual(record['session_totals'], {'confirmed_seconds': 7200, 'open_estimate_seconds': 0})
        self.assertEqual(record['sessions'][0]['ends_at'], self.mock_now.return_value)
        report = summary(self.scope, date(2026, 10, 10), date(2026, 10, 11))
        self.assertEqual(report['totals']['confirmed_inside_seconds'], 83 * 60)
        self.assertEqual(report['totals']['confirmed_outside_seconds'], 37 * 60)
        self.assertEqual(report['days'][0]['confirmed_inside_seconds'], 23 * 60)
        self.assertEqual(report['days'][1]['confirmed_inside_seconds'], 60 * 60)
        self.assertEqual(GoalCompletion.objects.count(), 1)

    def test_open_estimate_and_get_never_reconcile_or_confirm(self):
        self.template()
        self.at(10, 23, 37)
        session, _ = self.premium()
        self.at(11, 0, 15)
        value = agenda(self.scope, date(2026, 10, 10), date(2026, 10, 11))
        self.assertEqual(value['recorded_occurrences'][0]['session_totals'], {'confirmed_seconds': 0, 'open_estimate_seconds': 38 * 60})
        self.at(11, 2)
        value = agenda(self.scope, date(2026, 10, 10), date(2026, 10, 11))
        self.assertEqual(value['recorded_occurrences'][0]['session_totals'], {'confirmed_seconds': 0, 'open_estimate_seconds': 3600})
        session.refresh_from_db()
        self.assertEqual(session.state, 'running')
        self.assertEqual(GoalCompletion.objects.count(), 0)

    def test_suspension_including_previous_day_tail_excludes_link(self):
        self.template()
        self.at(11, 0, 15)
        routines.save_suspension(self.scope, {'expected_version': 1, 'date': date(2026, 10, 11), 'suspended': True})
        session, _ = self.premium()
        self.assertFalse(RoutineSessionAssociation.objects.exists())
        self.assertEqual(session.state, 'running')

    def test_exception_is_used_and_subsequent_cancel_preserves_recorded_occurrence(self):
        self.template()
        self.at(10, 18)
        row = next(r for r in routines.expand(self.scope, date(2026, 10, 10), date(2026, 10, 10))['occurrences'] if r['starts_at'].hour == 22 and r['origin_date'] == '2026-10-10')
        replacement = {'start_time': '23:00', 'end_time': '02:00', 'end_day_offset': 1, 'kind': 'gameplay', 'profile': 'general'}
        routines.save_exception(self.scope, {'expected_version': 1, 'origin_date': date(2026, 10, 10), 'block_id': row['block_id'], 'action': 'replace', 'replacement': replacement})
        self.at(10, 23, 37)
        session, _ = self.premium()
        link = self.assert_association(session)
        self.assertEqual(link.occurrence_starts_at.astimezone(routines.ZONE).hour, 23)
        routines.save_exception(self.scope, {'expected_version': 2, 'origin_date': date(2026, 10, 10), 'block_id': row['block_id'], 'action': 'cancel'})
        value = agenda(self.scope, date(2026, 10, 10), date(2026, 10, 11))
        self.assertFalse(any(r['occurrence_id'] == row['occurrence_id'] for r in value['occurrences']))
        self.assertEqual(value['recorded_occurrences'][0]['snapshots'][0]['starts_at'], link.occurrence_starts_at)

    def test_future_revision_does_not_move_existing_association(self):
        self.template()
        self.at(10, 23, 37)
        session, _ = self.premium()
        link = self.assert_association(session)
        routines.save_revision(self.scope, {'expected_version': 1, 'effective_from': date(2026, 10, 12), 'blocks': []})
        self.at(12, 23, 37)
        link.refresh_from_db()
        self.assertEqual(link.revision_version, 1)
        complete_schedule(session)
        later, _ = self.premium()
        self.assertFalse(RoutineSessionAssociation.objects.filter(schedule=later).exists())
        self.assertEqual(len(agenda(self.scope, date(2026, 10, 10), date(2026, 10, 11))['recorded_occurrences']), 1)

    def test_unclassified_queue_stays_unlinked_even_via_routines_start(self):
        self.template()
        row = self.row(day=10, hour=23, minute=37)
        self.choose(row)
        self.queue()
        response = self.client.post('/api/routines/start/', self.start_body(row, self.preview(row)), format='json')
        self.assertEqual(response.status_code, 201, response.data)
        self.assertIsNone(response.data['routine_occurrence'])
        self.assertEqual(response.data['routine_requested_occurrence']['occurrence_id'], row['occurrence_id'])
        self.assertFalse(RoutineSessionAssociation.objects.exists())

    def test_routines_start_distinguishes_future_preference_from_actual_block(self):
        self.template()
        self.at(10, 23, 37)
        future = next(r for r in routines.expand(self.scope, date(2026, 10, 11), date(2026, 10, 11))['occurrences'] if r['starts_at'].hour == 7)
        self.choose(future, 'premium')
        body = self.start_body(future, self.preview(future))
        response = self.client.post('/api/routines/start/', body, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        actual = response.data['routine_occurrence']
        self.assertEqual(actual['origin_date'], '2026-10-10')
        self.assertEqual(response.data['routine_requested_occurrence']['occurrence_id'], future['occurrence_id'])
        self.at(11, 8)
        retry = self.client.post('/api/routines/start/', body, format='json')
        self.assertEqual(retry.status_code, 200)
        self.assertEqual(retry.data['routine_occurrence'], actual)
        self.assertEqual(RoutineSessionAssociation.objects.count(), 1)

    def test_premium_retry_outside_block_never_creates_or_moves_binding(self):
        self.template()
        self.at(10, 23, 37)
        request_id = uuid4()
        original, _ = self.premium(request_id=request_id)
        self.at(11, 1, 15)
        retry, created = self.premium(request_id=request_id)
        self.assertFalse(created)
        self.assertEqual(retry.id, original.id)
        self.assert_association(retry)
        self.assertEqual(RoutineSessionAssociation.objects.count(), 1)

    def test_scope_isolation_and_no_backfill_of_old_records(self):
        self.template()
        self.at(10, 23, 37)
        session, _ = self.premium()
        self.assert_association(session)
        self.assertEqual(agenda('another-scope', date(2026, 10, 10), date(2026, 10, 11))['recorded_occurrences'], [])
        legacy = Schedule.objects.create(activity=self.activity, scope_key=self.scope, scheduled_date=date(2026, 10, 10), start_time='23:40', state='completed', starts_at=self.mock_now.return_value)
        agenda(self.scope, date(2026, 10, 10), date(2026, 10, 11))
        self.assertIsNone(ActivityExecutionSerializer(legacy).data['routine_occurrence'])
        self.assertEqual(RoutineSessionAssociation.objects.count(), 1)

    def test_deleting_plan_and_completed_schedule_retains_snapshot_and_fact(self):
        self.template()
        self.at(10, 23, 37)
        session, _ = self.premium()
        self.at(11, 0, 15)
        complete_schedule(session)
        RoutinePlan.objects.all().delete()
        session.delete()
        self.activity.name = 'Renomeado'
        self.activity.save()
        value = agenda(self.scope, date(2026, 10, 10), date(2026, 10, 11))
        record = value['recorded_occurrences'][0]
        self.assertEqual(record['sessions'][0]['activity_name'], 'Jogo manual')
        self.assertEqual(record['sessions'][0]['state'], 'completed')
        self.assertEqual(record['session_totals']['confirmed_seconds'], 38 * 60)

    def test_pagination_totals_include_all_sessions(self):
        self.template()
        self.at(10, 23, 37)
        session, _ = self.premium()
        original = self.assert_association(session)
        values = {field.name: getattr(original, field.name) for field in RoutineSessionAssociation._meta.fields if field.name not in ('id', 'schedule', 'source_schedule_id')}
        for i in range(50):
            RoutineSessionAssociation.objects.create(source_schedule_id=1000+i, **values)
        value = agenda(self.scope, date(2026, 10, 10), date(2026, 10, 11), sessions_page=2)
        record = value['recorded_occurrences'][0]
        self.assertEqual(len(record['sessions']), 1)
        self.assertEqual(record['sessions_pagination']['count'], 51)
        self.assertFalse(record['sessions_pagination']['has_next'])
        response = self.client.get('/api/routines/agenda/', {'date_from': '2026-10-10', 'date_to': '2026-10-11', 'sessions_page': 0})
        self.assertEqual(response.status_code, 400)

    def test_session_fixture_wire_responses(self):
        self.template()
        self.at(10, 23, 37)
        session, _ = self.premium(minutes=120)
        payload = {'contract_version': 1, 'examples': {}}
        execution_ids = {}
        def capture(name, value):
            wire = json.loads(JSONRenderer().render(value))
            def normalize(value):
                if isinstance(value, list):
                    return [normalize(item) for item in value]
                if isinstance(value, dict):
                    return {k: 101 if k == 'activity_id' else execution_ids.setdefault(v, 601 + len(execution_ids)) if k == 'execution_id' else normalize(v) for k,v in value.items()}
                return value
            payload['examples'][name] = normalize(wire)
        self.at(11, 0, 15)
        capture('saturday_occurrence_open_sunday', agenda(self.scope, date(2026, 10, 11), date(2026, 10, 11)))
        self.at(11, 1, 37)
        complete_schedule(session)
        capture('completed_overrun', agenda(self.scope, date(2026, 10, 10), date(2026, 10, 11)))
        capture('civil_summary', summary(self.scope, date(2026, 10, 10), date(2026, 10, 11)))
        self.at(11, 10)
        future = next(r for r in routines.expand(self.scope, date(2026, 10, 11), date(2026, 10, 11))['occurrences'] if r['starts_at'].hour == 14)
        routines.save_selection(self.scope, {'expected_version': self.version(), 'origin_date': date(2026, 10, 11), 'block_id': future['block_id'], 'source': 'premium', 'premium_period_id': self.p.id})
        response = self.client.post('/api/routines/start/', self.start_body(future, self.preview(future)), format='json')
        self.assertEqual(response.status_code, 201, response.data)
        capture('requested_vs_actual_execution_fields', {key: response.data[key] for key in ['execution_id', 'execution_origin', 'starts_at', 'routine_occurrence', 'routine_requested_occurrence']})
        capture('agenda_with_requested_vs_actual', agenda(self.scope, date(2026, 10, 11), date(2026, 10, 11)))
        path = Path(__file__).resolve().parents[2] / 'docs/contracts/spec-017-routine-sessions.json'
        if os.getenv('UPDATE_ROUTINE_FIXTURES') == '1':
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2)+'\n')
        self.assertEqual(json.loads(path.read_text()), payload)

    def test_retry_of_session_started_outside_plan_remains_unlinked(self):
        self.template()
        self.at(11, 12, 15)
        request_id = uuid4()
        original, _ = self.premium(minutes=180, request_id=request_id)
        self.at(11, 14, 45)
        retried, created = self.premium(minutes=180, request_id=request_id)
        self.assertFalse(created)
        self.assertEqual(retried.id, original.id)
        self.assertIsNone(ActivityExecutionSerializer(retried).data['routine_occurrence'])
        self.assertFalse(RoutineSessionAssociation.objects.exists())

    def test_queue_with_premium_period_is_gameplay_without_group_configuration(self):
        self.template()
        self.at(10, 23, 37)
        self.period()
        _, item = self.queue()
        session, _ = start_activity(activity=self.activity, queue_item=item, scope_key=self.scope)
        self.assertIsNotNone(session.premium_period_id)
        self.assert_association(session)

    def test_cancelled_occurrence_before_start_and_suspension_after_start(self):
        self.template()
        self.at(10, 23, 37)
        row = routines.context(self.scope)['current']
        session, _ = self.premium()
        routines.save_suspension(self.scope, {'expected_version': 1, 'date': date(2026, 10, 10), 'suspended': True})
        self.assertEqual(agenda(self.scope, date(2026, 10, 10), date(2026, 10, 10))['recorded_occurrences'][0]['sessions'][0]['execution_id'], session.id)
        complete_schedule(session)
        routines.save_suspension(self.scope, {'expected_version': 2, 'date': date(2026, 10, 10), 'suspended': False})
        routines.save_exception(self.scope, {'expected_version': 3, 'origin_date': date(2026, 10, 10), 'block_id': row['block_id'], 'action': 'cancel'})
        next_session, _ = self.premium()
        self.assertFalse(RoutineSessionAssociation.objects.filter(schedule=next_session).exists())

    def test_midnight_uses_origin_revision_even_after_new_revision_takes_effect(self):
        self.template()
        self.at(10, 18)
        routines.save_revision(self.scope, {'expected_version': 1, 'effective_from': date(2026, 10, 11), 'blocks': []})
        self.at(11, 0, 15)
        session, _ = self.premium()
        link = self.assert_association(session)
        self.assertEqual(link.revision_version, 1)
        self.assertEqual(agenda(self.scope, date(2026, 10, 11), date(2026, 10, 11))['recorded_occurrences'][0]['origin_date'], '2026-10-10')

    def test_utc_canonical_start_resolves_routine_local_date(self):
        from datetime import timezone as datetime_timezone
        self.template()
        self.mock_now.return_value = datetime(2026, 10, 11, 3, 15, tzinfo=datetime_timezone.utc)
        session, _ = self.premium()
        self.assert_association(session)
