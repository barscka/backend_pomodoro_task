import hashlib
import json
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APITestCase
from rest_framework_api_key.models import APIKey

from .models import (
    Activity, ActivityPreferenceEvent, ActivityQueue, ActivityQueueItem, Category, GoalCompletion,
    GameplayTrackingSettings, Group, History, PremiumPeriod, RoutinePlan, RoutineRevision, RoutineSelection, Schedule,
)
from .services import routines
from .services.routine_reporting import summary
from .services.routine_selection import preview, start_from_preview


@override_settings(PREMIUM_DIRECT_START_ENABLED=True)
class RoutineTests(APITestCase):
    def setUp(self):
        _, self.key = APIKey.objects.create_key(name='routine')
        self.auth = f'Api-Key {self.key}'
        self.scope = hashlib.sha256(self.auth.encode()).hexdigest()
        self.client.credentials(HTTP_AUTHORIZATION=self.auth)
        self.group = Group.objects.create(name='Games')
        self.category = Category.objects.create(name='Gameplay', group=self.group, max_daily_executions=20)
        self.activity = Activity.objects.create(name='Jogo manual', category=self.category, duration=60)
        self.base = datetime(2026, 10, 5, 0, 0, tzinfo=routines.ZONE)
        self.clock = patch('django.utils.timezone.now', return_value=self.base)
        self.mock_now = self.clock.start()
        self.addCleanup(self.clock.stop)

    def at(self, day, hour, minute=0):
        self.mock_now.return_value = datetime(2026, 10, day, hour, minute, tzinfo=routines.ZONE)

    def template(self):
        response = self.client.post('/api/routines/template/', {'expected_version': 0, 'effective_from': '2026-10-05'}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        return response.data

    def version(self):
        return RoutinePlan.objects.get(scope_key=self.scope).version

    def row(self, day=6, hour=19, minute=10):
        self.at(day, hour, minute)
        return routines.context(self.scope)['current']

    def period(self, **kwargs):
        return PremiumPeriod.objects.create(activity=self.activity, title='Premium', kind='paid',
            starts_on=kwargs.get('starts_on', date(2026, 10, 5)), ends_on=kwargs.get('ends_on', date(2026, 10, 12)))

    def choose(self, row, source='queue', **kwargs):
        data = {'expected_version': self.version(), 'origin_date': row['origin_date'], 'block_id': row['block_id'],
                'source': source, **({'group_id': self.group.id} if source == 'queue' else {'premium_period_id': self.period().id}), **kwargs}
        response = self.client.put('/api/routines/selection/', data, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        return data

    def queue(self, mode='normal'):
        queue = ActivityQueue.objects.create(scope_key=self.scope, group=self.group, mode=mode, skip_locked=mode == 'skipped_review')
        item = ActivityQueueItem.objects.create(queue=queue, activity=self.activity, position=1)
        return queue, item

    def preview(self, row):
        response = self.client.get('/api/routines/preview/', {'origin_date': row['origin_date'], 'block_id': row['block_id']})
        self.assertEqual(response.status_code, 200, response.data)
        return response.data

    def start_body(self, row, value):
        return {'origin_date': row['origin_date'], 'block_id': row['block_id'], 'preview_token': value['preview_token'], 'request_id': str(uuid4())}

    def test_template_totals_and_idempotency(self):
        self.template()
        again = self.client.post('/api/routines/template/', {'expected_version': 0, 'effective_from': '2026-10-05'}, format='json')
        self.assertEqual(again.status_code, 200)
        self.assertEqual(RoutineRevision.objects.count(), 1)
        agenda = routines.expand(self.scope, date(2026, 10, 5), date(2026, 10, 11))
        rows = agenda['occurrences']
        self.assertEqual(sum(r['duration_seconds'] for r in rows if r['kind'] == 'gameplay'), 42 * 3600)
        self.assertEqual(sum(r['duration_seconds'] for r in rows if r['profile'] == 'interruptible'), 6 * 3600)
        self.assertEqual(sum(r['duration_seconds'] for r in rows if r['kind'] == 'family'), int(22.5 * 3600))
        for d in range(5, 12):
            seconds = sum(r['duration_seconds'] for r in rows if r['origin_date'] == f'2026-10-{d:02}' and r['kind'] == 'gameplay')
            self.assertEqual(seconds, (4 if d < 10 else 11) * 3600)

    def test_tuesday_context_exclusive_boundary_and_free_weekend_gaps(self):
        self.template()
        self.row()
        response = self.client.get('/api/routines/context/')
        self.assertEqual(response.data['remaining_seconds'], 50 * 60)
        self.assertEqual(response.data['next_block']['kind'], 'family')
        self.row(hour=20, minute=0)
        self.assertEqual(routines.context(self.scope)['state'], 'family')
        for hour, minute in [(12, 15), (18, 45)]:
            self.at(10, hour, minute)
            self.assertEqual(routines.context(self.scope)['state'], 'free')

    def test_midnight_origin_and_week_boundary_no_duplicates(self):
        self.template()
        row = self.row(day=12, hour=0, minute=30)
        self.assertEqual(row['origin_date'], '2026-10-11')
        self.assertEqual(routines.context(self.scope)['remaining_seconds'], 1800)
        rows = routines.expand(self.scope, date(2026, 10, 12), date(2026, 10, 18))['occurrences']
        ids = [r['occurrence_id'] for r in rows]
        self.assertEqual(len(ids), len(set(ids)))
        daily = summary(self.scope, date(2026, 10, 12), date(2026, 10, 18))
        self.assertEqual(daily['totals']['general_seconds'] + daily['totals']['interruptible_seconds'], 42 * 3600)

    def test_no_plan_before_first_effective_date(self):
        self.template()
        self.at(4, 23)
        self.assertEqual(routines.context(self.scope)['state'], 'no_plan')
        value = summary(self.scope, date(2026, 10, 4), date(2026, 10, 4))
        self.assertIsNone(value['days'][0]['reference_seconds'])

    def test_casual_unknown_compatible_incompatible_and_manual_start(self):
        self.template()
        row = self.row(day=10, hour=10, minute=0)
        self.assertEqual(routines.suitability(self.scope, self.activity.id, row, 7200)['status'], 'insufficient_classification')
        payload = {'activity_id': self.activity.id, 'expected_version': 0, 'pause_immediately': True,
                   'online': False, 'requires_group': False, 'competitive': False}
        response = self.client.put('/api/routines/activity-preference/', payload, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(routines.suitability(self.scope, self.activity.id, row, 7200)['status'], 'recommended')
        self.assertEqual(self.client.get('/api/routines/recommendations/').data['count'], 1)
        payload.update(expected_version=1, online=True)
        self.client.put('/api/routines/activity-preference/', payload, format='json')
        self.assertEqual(self.client.get('/api/routines/recommendations/').data['count'], 0)
        self.choose(row, source='premium')
        value = self.preview(row)
        self.assertEqual(value['suitability']['status'], 'not_suitable')
        result = self.client.post('/api/routines/start/', self.start_body(row, value), format='json')
        self.assertEqual(result.status_code, 201, result.data)

    def test_profile_focused_minimum_and_version_conflict(self):
        self.template()
        row = self.row(day=10, hour=8, minute=0)
        data = {'activity_id': self.activity.id, 'expected_version': 0, 'focus_suitable': True, 'minimum_minutes': 90}
        self.client.put('/api/routines/activity-preference/', data, format='json')
        self.assertEqual(routines.suitability(self.scope, self.activity.id, row, 3600)['status'], 'not_suitable')
        self.assertEqual(self.client.put('/api/routines/activity-preference/', data, format='json').status_code, 409)

    def test_revisions_preserve_history_and_boundary_conflict(self):
        plan = self.template()
        blocks = plan['revisions'][0]['blocks']
        changed = [b for b in blocks if b['weekday'] != 1]
        response = self.client.patch('/api/routines/', {'expected_version': 1, 'effective_from': '2026-10-12', 'blocks': changed}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.row()
        self.assertEqual(routines.context(self.scope)['state'], 'gameplay')
        self.row(day=13)
        self.assertEqual(routines.context(self.scope)['state'], 'free')
        self.assertEqual(RoutineRevision.objects.count(), 2)

    def test_weekly_overlap_including_sunday_tail_is_rejected(self):
        data = self.template()
        blocks = data['revisions'][0]['blocks']
        blocks.append({'block_id': str(uuid4()), 'weekday': 0, 'kind': 'gameplay', 'profile': 'general', 'start_time': '00:00', 'end_time': '00:45', 'end_day_offset': 0})
        response = self.client.patch('/api/routines/', {'expected_version': 1, 'effective_from': '2026-10-12', 'blocks': blocks}, format='json')
        self.assertEqual(response.status_code, 409, response.data)
        self.assertEqual(response.data['code'], 'overlap')
        self.assertEqual(self.version(), 1)

    def test_revision_boundary_overlap(self):
        data = self.template()
        blocks = [b for b in data['revisions'][0]['blocks'] if not (b['end_day_offset'] == 1)]
        blocks.append({'block_id': str(uuid4()), 'weekday': 0, 'kind': 'gameplay', 'profile': 'general', 'start_time': '00:00', 'end_time': '00:45', 'end_day_offset': 0})
        response = self.client.patch('/api/routines/', {'expected_version': 1, 'effective_from': '2026-10-12', 'blocks': blocks}, format='json')
        self.assertEqual(response.status_code, 409)
        self.assertEqual(RoutineRevision.objects.count(), 1)

    def test_cancel_shorten_replace_move_and_overlap_rollback(self):
        self.template()
        row = self.row()
        base = {'expected_version': 1, 'origin_date': row['origin_date'], 'block_id': row['block_id']}
        replacement = {'start_time': '18:30', 'end_time': '19:30', 'end_day_offset': 0, 'kind': 'gameplay', 'profile': 'general'}
        value = self.client.put('/api/routines/exception/', {**base, 'action': 'shorten', 'replacement': replacement}, format='json')
        self.assertEqual(value.status_code, 200, value.data)
        self.assertEqual(routines.context(self.scope)['remaining_seconds'], 20 * 60)
        replacement.update(start_time='19:30', end_time='21:00')
        result = self.client.put('/api/routines/exception/', {**base, 'expected_version': 2, 'action': 'replace', 'replacement': replacement}, format='json')
        self.assertEqual(result.status_code, 409)
        self.assertEqual(self.version(), 2)
        replacement.update(start_time='17:00', end_time='18:00')
        value = self.client.put('/api/routines/exception/', {**base, 'expected_version': 2, 'action': 'replace', 'replacement': replacement}, format='json')
        self.assertEqual(value.status_code, 200)
        replacement.update(start_time='16:00', end_time='17:30')
        value = self.client.put('/api/routines/exception/', {**base, 'expected_version': 3, 'action': 'move', 'replacement': replacement}, format='json')
        self.assertEqual(value.status_code, 200, value.data)
        result = self.client.put('/api/routines/exception/', {**base, 'expected_version': 4, 'action': 'cancel'}, format='json')
        self.assertEqual(result.status_code, 200)
        self.assertEqual(routines.context(self.scope)['state'], 'free')
        self.row(day=13)
        self.assertEqual(routines.context(self.scope)['state'], 'gameplay')

    def test_suspension_includes_yesterday_tail_and_resume(self):
        self.template()
        self.at(6, 0, 30)
        response = self.client.put('/api/routines/suspension/', {'expected_version': 1, 'date': '2026-10-06', 'suspended': True}, format='json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(routines.context(self.scope)['state'], 'suspended')
        report = summary(self.scope, date(2026, 10, 6), date(2026, 10, 6))
        self.assertIsNone(report['days'][0]['reference_seconds'])
        self.assertEqual(report['totals']['general_seconds'], 0)
        response = self.client.put('/api/routines/suspension/', {'expected_version': 2, 'date': '2026-10-06', 'suspended': False}, format='json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(routines.context(self.scope)['state'], 'gameplay')

    def test_stale_version_and_retroactive_edits(self):
        data = self.template()
        response = self.client.patch('/api/routines/', {'expected_version': 0, 'effective_from': '2026-10-12', 'blocks': []}, format='json')
        self.assertEqual(response.status_code, 409)
        response = self.client.patch('/api/routines/', {'expected_version': 1, 'effective_from': '2026-10-05', 'blocks': []}, format='json')
        self.assertEqual(response.status_code, 400)
        self.at(6, 10)
        response = self.client.put('/api/routines/suspension/', {'expected_version': 1, 'date': '2026-10-05', 'suspended': True}, format='json')
        self.assertEqual(response.status_code, 400)

    def test_queue_preview_does_not_present_or_create_or_reconcile(self):
        self.template()
        row = self.row()
        self.choose(row)
        self.assertEqual(self.preview(row)['availability'], 'queue_absent')
        queue, item = self.queue()
        Schedule.objects.create(activity=self.activity, scope_key=self.scope, scheduled_date=date(2026, 10, 6),
            start_time='18:00', starts_at=self.mock_now.return_value - timedelta(hours=2), expected_end_at=self.mock_now.return_value - timedelta(hours=1))
        before = list(ActivityQueueItem.objects.values())
        for _ in range(2):
            value = self.preview(row)
            self.assertEqual(value['availability'], 'active_execution_conflict')
            self.client.get('/api/routines/agenda/', {'date_from': '2026-10-05', 'date_to': '2026-10-11'})
            self.client.get('/api/routines/context/')
            self.client.get('/api/routines/summary/', {'date_from': '2026-10-05', 'date_to': '2026-10-11'})
        self.assertEqual(list(ActivityQueueItem.objects.values()), before)
        self.assertEqual(ActivityQueue.objects.count(), 1)
        self.assertEqual(Schedule.objects.get().state, 'running')
        self.assertEqual(GoalCompletion.objects.count(), 0)
        self.assertEqual(ActivityPreferenceEvent.objects.count(), 0)

    def test_canonical_presented_item_precedes_lower_pending_position(self):
        self.template()
        row = self.row()
        self.choose(row)
        queue, item = self.queue()
        other = Activity.objects.create(name='Apresentada', category=self.category)
        presented = ActivityQueueItem.objects.create(queue=queue, activity=other, position=2, state='presented')
        self.assertEqual(self.preview(row)['queue']['item_id'], presented.id)
        item.refresh_from_db()
        self.assertIsNone(item.presented_at)

    def test_queue_modes_empty_review_and_expired_preference(self):
        self.template()
        row = self.row()
        self.choose(row)
        queue, item = self.queue('skipped_review')
        value = self.preview(row)
        self.assertEqual(value['queue']['mode'], 'skipped_review')
        self.assertTrue(value['queue']['skip_locked'])
        item.state = 'completed'
        item.save()
        self.assertEqual(self.preview(row)['availability'], 'review_blocked')
        queue.mode = 'normal'
        queue.save()
        self.assertEqual(self.preview(row)['availability'], 'queue_empty')
        self.choose(row, 'premium', premium_period_id=self.period(ends_on=date(2026, 10, 5)).id)
        self.assertEqual(self.preview(row)['availability'], 'premium_not_active')

    def test_changed_next_item_returns_updated_preview_without_start(self):
        self.template()
        row = self.row()
        self.choose(row)
        queue, item = self.queue()
        value = self.preview(row)
        other = Activity.objects.create(name='Segundo', category=self.category)
        new_item = ActivityQueueItem.objects.create(queue=queue, activity=other, position=2)
        item.state = 'skipped'
        item.save()
        response = self.client.post('/api/routines/start/', self.start_body(row, value), format='json')
        self.assertEqual(response.status_code, 409, response.data)
        self.assertEqual(response.data['code'], 'stale_preview')
        self.assertEqual(response.data['preview']['queue']['item_id'], new_item.id)
        self.assertFalse(Schedule.objects.exists())
        new_item.refresh_from_db()
        self.assertEqual(new_item.state, 'pending')

    def test_context_change_between_preview_start_and_cancelled_occurrence(self):
        self.template()
        row = self.row()
        self.choose(row)
        self.queue()
        value = self.preview(row)
        body = self.start_body(row, value)
        self.at(6, 20)
        self.assertEqual(self.client.post('/api/routines/start/', body, format='json').data['code'], 'stale_preview')
        self.at(6, 19, 10)
        self.client.put('/api/routines/exception/', {'expected_version': self.version(), 'origin_date': row['origin_date'], 'block_id': row['block_id'], 'action': 'cancel'}, format='json')
        self.assertEqual(self.client.post('/api/routines/start/', body, format='json').data['code'], 'stale_preview')

    def test_premium_start_suggests_duration_preserves_activity_and_replays(self):
        self.template()
        row = self.row()
        self.choose(row, 'premium', return_group_id=self.group.id)
        value = self.preview(row)
        self.assertEqual(value['suggested_duration_minutes'], 50)
        self.assertFalse(value['crosses_next_block'])
        body = self.start_body(row, value)
        first = self.client.post('/api/routines/start/', body, format='json')
        self.assertEqual(first.status_code, 201, first.data)
        self.assertEqual(first.data['execution_origin'], 'premium_direct')
        self.assertEqual(Schedule.objects.get().planned_duration_seconds, 3000)
        self.assertEqual(Schedule.objects.get().return_group_id, self.group.id)
        self.activity.refresh_from_db()
        self.assertEqual(self.activity.duration, 60)
        self.assertEqual(self.client.post('/api/routines/start/', body, format='json').status_code, 200)
        body['duration_minutes'] = 60
        self.assertEqual(self.client.post('/api/routines/start/', body, format='json').data['code'], 'idempotency_payload_conflict')
        self.assertEqual(History.objects.count(), 1)
        self.assertFalse(ActivityQueue.objects.exists())

    def test_queue_start_preserves_duration_overrun_and_goal_completion(self):
        self.template()
        row = self.row()
        self.choose(row)
        queue, item = self.queue('skipped_review')
        value = self.preview(row)
        self.assertEqual(value['duration_minutes'], 60)
        self.assertTrue(value['crosses_next_block'])
        body = self.start_body(row, value)
        first = self.client.post('/api/routines/start/', body, format='json')
        self.assertEqual(first.status_code, 201, first.data)
        self.assertEqual(Schedule.objects.get().execution_origin, 'queue')
        self.assertEqual(self.client.post('/api/routines/start/', body, format='json').status_code, 200)
        self.at(6, 19, 40)
        schedule = Schedule.objects.get()
        from .services.activity_execution import complete_schedule
        complete_schedule(schedule)
        self.assertEqual(GoalCompletion.objects.count(), 1)
        self.assertEqual(History.objects.count(), 1)

    def test_queue_custom_duration_forbidden(self):
        self.template()
        row = self.row()
        self.choose(row)
        self.queue()
        value = self.preview(row)
        body = {**self.start_body(row, value), 'duration_minutes': 10}
        response = self.client.post('/api/routines/start/', body, format='json')
        self.assertEqual(response.data['code'], 'queue_duration_fixed')
        self.assertFalse(Schedule.objects.exists())

    def test_source_premium_expiry_and_profile_changes_invalidate_token(self):
        self.template()
        row = self.row()
        chosen = self.choose(row, 'premium')
        value = self.preview(row)
        p = PremiumPeriod.objects.get(pk=chosen['premium_period_id'])
        p.ends_on = date(2026, 10, 5)
        p.save()
        response = self.client.post('/api/routines/start/', self.start_body(row, value), format='json')
        self.assertEqual(response.data['code'], 'stale_preview')
        self.assertFalse(Schedule.objects.exists())

    def test_scope_isolation_and_api_key_required(self):
        self.template()
        row = self.row()
        self.choose(row, 'premium')
        value = self.preview(row)
        _, key = APIKey.objects.create_key(name='other')
        self.client.credentials(HTTP_AUTHORIZATION=f'Api-Key {key}')
        self.assertEqual(self.client.get('/api/routines/').data['state'], 'no_plan')
        self.assertEqual(self.client.get('/api/routines/activity-preference/', {'activity_id': self.activity.id}).data['version'], 0)
        self.assertEqual(self.client.get('/api/routines/preview/', {'origin_date': row['origin_date'], 'block_id': row['block_id']}).status_code, 404)
        self.assertEqual(self.client.get('/api/routines/selection/', {'origin_date': row['origin_date'], 'block_id': row['block_id']}).data['selection'], None)
        self.client.credentials()
        self.assertEqual(self.client.get('/api/routines/').status_code, 403)

    def fact(self, start, seconds, *, origin='premium_direct', scope=None, precise=True):
        return GoalCompletion.objects.create(source_schedule_id=1000 + GoalCompletion.objects.count(), scope_key=scope or self.scope,
            category_id_snapshot=self.category.id, group_id_snapshot=self.group.id, activity_id_snapshot=self.activity.id,
            completed_at=start + timedelta(seconds=seconds), duration_minutes=seconds // 60,
            started_at=start if precise else None, duration_seconds=seconds if precise else None,
            execution_origin=origin, context_source='execution_start')

    def test_summary_intersections_open_estimate_and_unknown_coverage(self):
        self.template()
        self.at(6, 19, 30)
        self.fact(datetime(2026, 10, 6, 19, 30, tzinfo=routines.ZONE), 3600)
        self.fact(datetime(2026, 10, 6, 15, 0, tzinfo=routines.ZONE), 1800)
        self.fact(datetime(2026, 10, 6, 16, 0, tzinfo=routines.ZONE), 1800, scope='other')
        self.fact(datetime(2026, 10, 4, 15, 0, tzinfo=routines.ZONE), 1800)
        self.fact(datetime(2026, 10, 6, 17, 0, tzinfo=routines.ZONE), 1800, precise=False)
        Schedule.objects.create(activity=self.activity, scope_key=self.scope, scheduled_date=date(2026, 10, 6), start_time='19:00',
            starts_at=datetime(2026, 10, 6, 19, 0, tzinfo=routines.ZONE), expected_end_at=datetime(2026, 10, 6, 20, 0, tzinfo=routines.ZONE), execution_origin='premium_direct')
        data = summary(self.scope, date(2026, 10, 4), date(2026, 10, 6))
        self.assertEqual(data['totals']['confirmed_inside_seconds'], 1800)
        self.assertEqual(data['totals']['confirmed_outside_seconds'], 3600)
        self.assertEqual(data['totals']['confirmed_uncovered_seconds'], 1800)
        self.assertEqual(data['totals']['open_estimate_seconds'], 1800)
        self.assertEqual(data['totals']['insufficient_precision_count'], 1)
        self.assertEqual(data['coverage'], 'partial')

    def test_daily_midnight_split_casual_and_study_excluded(self):
        self.template()
        self.at(11, 10)
        self.fact(datetime(2026, 10, 10, 23, 30, tzinfo=routines.ZONE), 7200)
        self.fact(datetime(2026, 10, 10, 10, 0, tzinfo=routines.ZONE), 1800)
        self.fact(datetime(2026, 10, 10, 15, 0, tzinfo=routines.ZONE), 1800, origin='queue')
        data = summary(self.scope, date(2026, 10, 10), date(2026, 10, 11))
        self.assertEqual(data['totals']['confirmed_inside_seconds'], 7200)
        self.assertEqual(data['totals']['confirmed_outside_seconds'], 1800)
        self.assertEqual(data['totals']['confirmed_interruptible_seconds'], 1800)
        self.assertEqual(data['days'][0]['confirmed_inside_seconds'], 3600)
        self.assertEqual(data['days'][1]['confirmed_inside_seconds'], 3600)

    def test_optional_premium_reference_default_and_unknown_suspension(self):
        self.template()
        self.at(12, 10)
        period = self.period(ends_on=date(2026, 10, 20))
        before = self.client.get('/api/premium-analytics/summary/', {'date_from': '2026-10-05', 'date_to': '2026-10-11'})
        self.assertEqual(before.data['reference_seconds'], 7 * 300 * 60)
        self.assertEqual(before.data['reference_source'], 'fixed_daily')
        response = self.client.patch('/api/gameplay-tracking-settings/', {'expected_version': 0, 'reference_source': 'routine'}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        after = self.client.get('/api/premium-analytics/summary/', {'date_from': '2026-10-12', 'date_to': '2026-10-18'})
        self.assertEqual(after.data['reference_seconds'], 42 * 3600)
        self.assertEqual(after.data['reference_denominators']['interruptible_seconds'], 6 * 3600)
        self.client.put('/api/routines/suspension/', {'expected_version': 1, 'date': '2026-10-12', 'suspended': True}, format='json')
        data = self.client.get('/api/premium-analytics/summary/', {'date_from': '2026-10-12', 'date_to': '2026-10-18'})
        self.assertIsNone(data.data['reference_seconds'])
        self.assertEqual(data.data['reference_denominators']['coverage'], 'partial')
        from .services.premium_reporting import period_stats
        self.assertIsNone(period_stats(period, self.scope)['elapsed_reference_seconds'])

    def test_contract_validation_interval_unknown_scope_and_versions(self):
        self.assertEqual(self.client.post('/api/routines/template/', {'expected_version': 0, 'effective_from': '2026-10-05', 'scope_key': 'forged'}, format='json').status_code, 400)
        self.template()
        for query in [{'date_from': '2026-10-01', 'date_to': '2026-11-01'}, {'date_from': 'bad', 'date_to': '2026-10-01'}, {'date_from': '2026-10-02', 'date_to': '2026-10-01'}]:
            self.assertEqual(self.client.get('/api/routines/agenda/', query).status_code, 400)
        self.assertEqual(self.client.patch('/api/gameplay-tracking-settings/', {'expected_version': 'bad'}, format='json').status_code, 400)

    def test_no_implicit_creation_on_any_empty_read(self):
        for path, query in [('routines', {}), ('routines/context', {}), ('routines/agenda', {'date_from': '2026-10-05', 'date_to': '2026-10-11'}), ('routines/recommendations', {}), ('routines/summary', {'date_from': '2026-10-05', 'date_to': '2026-10-11'})]:
            self.assertEqual(self.client.get(f'/api/{path}/', query).status_code, 200)
        self.assertFalse(RoutinePlan.objects.exists())
        self.assertFalse(Schedule.objects.exists())
        self.assertFalse(ActivityQueue.objects.exists())

    def test_flutter_contract_fixtures(self):
        """Golden wire responses; regeneration requires an explicit test-only switch."""
        import os
        from rest_framework.renderers import JSONRenderer
        samples = {}
        def capture(name, response):
            self.assertLess(response.status_code, 500, response.data)
            wire = json.loads(JSONRenderer().render(response.data))
            def strip_tokens(value, parent=''):
                if isinstance(value, dict):
                    result = {}
                    for key, item in value.items():
                        if key == 'preview_token':
                            result[key] = '<signed-preview-token>'
                        elif item is not None and key in ('activity_id', 'group_id', 'return_group_id', 'premium_period_id', 'item_id'):
                            result[key] = {'activity_id': 101, 'group_id': 201, 'return_group_id': 201, 'premium_period_id': 301, 'item_id': 501}[key]
                        elif key == 'id' and parent in ('activity', 'queue', 'premium_period'):
                            result[key] = {'activity': 101, 'queue': 401, 'premium_period': 301}[parent]
                        else:
                            result[key] = strip_tokens(item, key)
                    return result
                if isinstance(value, list):
                    return [strip_tokens(item, parent) for item in value]
                return value
            samples[name] = {'status': response.status_code, 'body': strip_tokens(wire)}
        capture('no_plan', self.client.get('/api/routines/'))
        capture('initial_template', self.client.post('/api/routines/template/', {'expected_version': 0, 'effective_from': '2026-10-05'}, format='json'))
        row = self.row()
        capture('weekday_1910', self.client.get('/api/routines/context/'))
        self.choose(row)
        capture('queue_absent', self.client.get('/api/routines/preview/', {'origin_date': row['origin_date'], 'block_id': row['block_id']}))
        queue, item = self.queue()
        capture('queue_normal', self.client.get('/api/routines/preview/', {'origin_date': row['origin_date'], 'block_id': row['block_id']}))
        old = self.preview(row)
        queue.mode, queue.skip_locked = 'skipped_review', True
        queue.save()
        capture('queue_review', self.client.get('/api/routines/preview/', {'origin_date': row['origin_date'], 'block_id': row['block_id']}))
        capture('stale_preview', self.client.post('/api/routines/start/', self.start_body(row, old), format='json'))
        item.state = 'completed'
        item.save()
        capture('review_blocked', self.client.get('/api/routines/preview/', {'origin_date': row['origin_date'], 'block_id': row['block_id']}))
        self.choose(row, 'premium')
        capture('premium_active', self.client.get('/api/routines/preview/', {'origin_date': row['origin_date'], 'block_id': row['block_id']}))
        period = PremiumPeriod.objects.get()
        period.ends_on = date(2026, 10, 5)
        period.save()
        capture('premium_expired', self.client.get('/api/routines/preview/', {'origin_date': row['origin_date'], 'block_id': row['block_id']}))
        capture('version_conflict', self.client.put('/api/routines/suspension/', {'expected_version': 0, 'date': '2026-10-06', 'suspended': True}, format='json'))
        self.at(10, 10)
        capture('casual_context', self.client.get('/api/routines/context/'))
        self.client.put('/api/routines/activity-preference/', {'expected_version': 0, 'activity_id': self.activity.id,
            'pause_immediately': True, 'online': False, 'requires_group': False, 'competitive': False}, format='json')
        capture('casual_recommended', self.client.get('/api/routines/recommendations/'))
        capture('weekend', self.client.get('/api/routines/agenda/', {'date_from': '2026-10-10', 'date_to': '2026-10-11'}))
        self.at(11, 12, 15)
        capture('free', self.client.get('/api/routines/context/'))
        self.at(12, 0, 30)
        capture('midnight', self.client.get('/api/routines/context/'))
        self.client.put('/api/routines/suspension/', {'expected_version': self.version(), 'date': '2026-10-12', 'suspended': True}, format='json')
        capture('suspended', self.client.get('/api/routines/context/'))
        contract = {'contract_version': 1, 'timezone': 'America/Sao_Paulo', 'examples': samples}
        path = Path(__file__).resolve().parents[2] / 'docs/contracts/spec-017-routines.json'
        if os.getenv('UPDATE_ROUTINE_FIXTURES') == '1':
            path.write_text(json.dumps(contract, ensure_ascii=False, indent=2) + '\n')
        self.assertEqual(json.loads(path.read_text()), contract)

    def test_initial_capacity_payload_and_cardio_expectation(self):
        data = self.template()
        capacity = data['revisions'][0]['nominal_capacity']
        self.assertEqual(capacity['gameplay_seconds'], 42 * 3600)
        self.assertEqual(capacity['family_seconds'], 81000)
        self.assertEqual(capacity['gameplay_seconds_by_weekday'], [14400] * 5 + [39600] * 2)
        self.at(6, 18, 10)
        self.assertEqual(routines.context(self.scope)['current']['cardio_expectation'], {'minimum_minutes': 15, 'maximum_minutes': 30})

    def test_previous_day_selection_while_occurrence_is_active(self):
        self.template()
        row = self.row(day=6, hour=0, minute=30)
        self.assertEqual(row['origin_date'], '2026-10-05')
        self.choose(row, 'premium')
        self.assertEqual(RoutineSelection.objects.get().origin_date, date(2026, 10, 5))
        self.assertEqual(self.preview(row)['suggested_duration_minutes'], 30)

    def test_invalid_signature_and_expired_preview_include_context(self):
        self.template()
        row = self.row()
        self.choose(row)
        self.queue()
        value = self.preview(row)
        body = self.start_body(row, value)
        body['preview_token'] += 'tampered'
        response = self.client.post('/api/routines/start/', body, format='json')
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data['code'], 'stale_preview')
        self.assertIn('preview', response.data)
        with patch('django.core.signing.time.time', return_value=0):
            old = self.preview(row)
        response = self.client.post('/api/routines/start/', self.start_body(row, old), format='json')
        self.assertEqual(response.data['code'], 'stale_preview')
        self.assertFalse(Schedule.objects.exists())

    def test_forecast_expiring_before_future_occurrence_and_flag(self):
        self.template()
        # Save for next Tuesday while this week's Premium is still active.
        self.at(6, 19, 10)
        future = next(r for r in routines.expand(self.scope, date(2026, 10, 13), date(2026, 10, 13))['occurrences'] if r['kind'] == 'gameplay' and r['starts_at'].hour == 18)
        self.choose(future, 'premium')
        value = self.preview(future)
        self.assertFalse(value['available_at_occurrence_start'])
        with override_settings(PREMIUM_DIRECT_START_ENABLED=False):
            self.assertEqual(self.preview(future)['availability'], 'premium_direct_disabled')
        self.at(13, 19, 10)
        self.assertEqual(self.preview(future)['availability'], 'premium_not_active')

    def test_unknown_dates_and_non_gameplay_selection_are_rejected(self):
        self.assertEqual(self.client.get('/api/routines/agenda/', {'date_from': '9999-12-31', 'date_to': '9999-12-31'}).status_code, 400)
        self.template()
        row = self.row(day=6, hour=20, minute=30)
        response = self.client.put('/api/routines/selection/', {'origin_date': row['origin_date'], 'block_id': row['block_id'], 'expected_version': self.version(), 'source': 'queue', 'group_id': self.group.id}, format='json')
        self.assertEqual(response.status_code, 400)

    def test_classification_changed_since_preview_is_conflict(self):
        self.template()
        row = self.row()
        self.choose(row)
        self.queue()
        value = self.preview(row)
        self.client.put('/api/routines/activity-preference/', {'activity_id': self.activity.id, 'expected_version': 0, 'minimum_minutes': 20}, format='json')
        response = self.client.post('/api/routines/start/', self.start_body(row, value), format='json')
        self.assertEqual(response.data['code'], 'stale_preview')
        self.assertFalse(Schedule.objects.exists())

    def test_completed_schedule_without_fact_is_insufficient_coverage(self):
        self.template()
        self.at(6, 19, 30)
        Schedule.objects.create(activity=self.activity, scope_key=self.scope, scheduled_date=date(2026, 10, 6), start_time='19:00',
            state='completed', completed=True, execution_origin='premium_direct', completed_at=self.mock_now.return_value)
        result = summary(self.scope, date(2026, 10, 6), date(2026, 10, 6))
        self.assertEqual(result['totals']['insufficient_precision_count'], 1)
        self.assertEqual(result['coverage'], 'partial')
        self.assertEqual(result['totals']['confirmed_inside_seconds'], 0)

    def test_configured_queue_gameplay_and_suspended_time_are_uncovered(self):
        self.template()
        self.at(6, 19, 30)
        config = GameplayTrackingSettings.objects.create(scope_key=self.scope)
        config.groups.add(self.group)
        self.fact(datetime(2026, 10, 6, 19, 0, tzinfo=routines.ZONE), 1800, origin='queue')
        self.assertEqual(summary(self.scope, date(2026, 10, 6), date(2026, 10, 6))['totals']['confirmed_inside_seconds'], 1800)
        self.client.put('/api/routines/suspension/', {'expected_version': 1, 'date': '2026-10-06', 'suspended': True}, format='json')
        value = summary(self.scope, date(2026, 10, 6), date(2026, 10, 6))
        self.assertEqual(value['totals']['confirmed_outside_seconds'], 0)
        self.assertEqual(value['totals']['confirmed_uncovered_seconds'], 1800)

    def test_existing_settings_error_codes_and_partial_groups_are_preserved(self):
        response = self.client.patch('/api/gameplay-tracking-settings/', {'daily_reference_minutes': 300}, format='json')
        self.assertEqual(response.data['code'], 'expected_version_required')
        response = self.client.patch('/api/gameplay-tracking-settings/', {'expected_version': 0, 'daily_reference_minutes': 0}, format='json')
        self.assertEqual(response.data['code'], 'invalid_daily_reference')
        config = GameplayTrackingSettings.objects.create(scope_key=self.scope)
        config.groups.add(self.group)
        response = self.client.patch('/api/gameplay-tracking-settings/', {'expected_version': 1, 'reference_source': 'routine'}, format='json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['group_ids'], [self.group.id])

    def test_queue_preview_preserves_direct_session_and_skipped_review_eligibility(self):
        self.template()
        row = self.row()
        self.choose(row)
        queue, _ = self.queue()
        previous = Schedule.objects.create(activity=self.activity, scope_key=self.scope, scheduled_date=date(2026, 10, 6),
            start_time='17:00', state='completed', completed=True, execution_origin='premium_direct')
        History.objects.create(activity=self.activity, schedule=previous,
            start_time=datetime(2026, 10, 6, 17, 0, tzinfo=routines.ZONE), end_time=datetime(2026, 10, 6, 17, 30, tzinfo=routines.ZONE), duration=30)
        self.assertEqual(self.preview(row)['availability'], 'available')
        previous.execution_origin = 'queue'
        previous.save()
        self.assertEqual(self.preview(row)['availability'], 'queue_ineligible')
        queue.mode, queue.skip_locked = 'skipped_review', True
        queue.save()
        self.assertEqual(self.preview(row)['availability'], 'available')
        response = self.client.post('/api/routines/start/', self.start_body(row, self.preview(row)), format='json')
        self.assertEqual(response.status_code, 201, response.data)
