from decimal import Decimal
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from datetime import timedelta
from pokertracker.addresses import split_address
from .models import Location, PokerSession

class PokerSessionTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="alice", password="StrongPassword123!")
        self.other = get_user_model().objects.create_user(username="bob", password="StrongPassword123!")
        self.client.force_login(self.user)

    def test_create_and_crud(self):
        response = self.client.post(reverse("session-create"), {
            "game": "No-Limit Hold'em", "buy_in": "200.00",
            "stakes": "$1/$2", "new_location_name": "Home",
            "new_location_street": "123 Main St", "new_location_city": "Springfield",
            "new_location_state": "IL", "new_location_postal_code": "62701", "new_location_rating": "5", "notes": ""
        })
        self.assertEqual(response.status_code, 302)
        session = PokerSession.objects.get(user=self.user)
        self.assertEqual(session.status, PokerSession.RUNNING)
        self.assertEqual(session.location.name, "Home")
        self.assertEqual(session.location.full_address, "123 Main St, Springfield, IL 62701, United States")
        detail = self.client.get(session.get_absolute_url())
        self.assertContains(detail, session.location.map_embed_url.replace("&", "&amp;"))
        self.assertContains(detail, "Open in Maps")
        self.assertEqual(session.location.rating, 5)
        self.assertEqual(self.client.get(session.get_absolute_url()).status_code, 200)
        self.assertEqual(self.client.post(reverse("session-update", args=[session.pk]), {
            "game": "Pot-Limit Omaha", "buy_in": "200.00",
            "stakes": "$2/$5", "location": str(session.location_id),
            "notes": "", "cash_out": ""
        }).status_code, 302)
        session.refresh_from_db()
        self.assertEqual(session.game, "Pot-Limit Omaha")
        self.assertEqual(self.client.post(reverse("session-delete", args=[session.pk])).status_code, 302)
        self.assertFalse(PokerSession.objects.filter(pk=session.pk).exists())

    def test_edit_running_session_time_and_timer_continues(self):
        session = PokerSession.objects.create(
            user=self.user,
            accumulated_seconds=30,
            resumed_at=timezone.now() - timedelta(minutes=5),
        )
        response = self.client.post(reverse("session-update", args=[session.pk]), {
            "game": "No-Limit Hold'em", "buy_in": "0.00",
            "stakes": "$2/$5", "location": "", "notes": "", "cash_out": "",
            "time_played": "01:23:45",
        })
        self.assertEqual(response.status_code, 302)
        session.refresh_from_db()
        self.assertEqual(session.accumulated_seconds, 5025)
        self.assertGreaterEqual(session.resumed_at, timezone.now() - timedelta(seconds=5))
        self.assertLessEqual(session.elapsed_seconds, 5030)

    def test_edit_paused_session_time(self):
        session = PokerSession.objects.create(
            user=self.user,
            status=PokerSession.PAUSED,
            accumulated_seconds=120,
            resumed_at=None,
        )
        response = self.client.post(reverse("session-update", args=[session.pk]), {
            "game": "No-Limit Hold'em", "buy_in": "0.00",
            "stakes": "$2/$5", "location": "", "notes": "", "cash_out": "",
            "time_played": "02:00:00",
        })
        self.assertEqual(response.status_code, 302)
        session.refresh_from_db()
        self.assertEqual(session.elapsed_seconds, 7200)
        self.assertIsNone(session.resumed_at)

    def test_edit_session_rejects_invalid_time_format(self):
        session = PokerSession.objects.create(user=self.user, accumulated_seconds=120)
        response = self.client.post(reverse("session-update", args=[session.pk]), {
            "game": "No-Limit Hold'em", "buy_in": "0.00",
            "stakes": "$2/$5", "location": "", "notes": "", "cash_out": "",
            "time_played": "01:75:00",
        })
        self.assertEqual(response.status_code, 200)
        session.refresh_from_db()
        self.assertEqual(session.accumulated_seconds, 120)

    def test_timer_pause_resume_finish(self):
        session = PokerSession.objects.create(user=self.user, buy_in=Decimal("100"),
            resumed_at=timezone.now()-timedelta(minutes=5))
        pause = reverse("session-action", args=[session.pk, "pause"])
        self.assertEqual(self.client.post(pause).status_code, 302)
        session.refresh_from_db()
        self.assertEqual(session.status, PokerSession.PAUSED)
        self.assertIsNone(session.resumed_at)
        self.assertGreaterEqual(session.accumulated_seconds, 299)
        self.assertEqual(self.client.post(pause).status_code, 400)
        self.assertEqual(self.client.post(reverse("session-action", args=[session.pk, "resume"])).status_code, 302)
        self.assertEqual(self.client.post(reverse("session-action", args=[session.pk, "finish"]), {"cash_out": "175.50"}).status_code, 302)
        session.refresh_from_db()
        self.assertEqual(session.status, PokerSession.FINISHED)
        self.assertEqual(session.profit, Decimal("75.50"))
        self.assertIsNotNone(session.ended_at)
        self.assertEqual(self.client.post(reverse("session-action", args=[session.pk, "resume"])).status_code, 400)

    def test_invalid_cash_out_does_not_finish(self):
        session = PokerSession.objects.create(user=self.user, buy_in=100)
        url = reverse("session-action", args=[session.pk, "finish"])
        self.assertEqual(self.client.post(url, {"cash_out": "-3"}).status_code, 400)
        session.refresh_from_db()
        self.assertEqual(session.status, PokerSession.RUNNING)

    def test_user_isolation(self):
        session = PokerSession.objects.create(user=self.other)
        self.assertEqual(self.client.get(session.get_absolute_url()).status_code, 404)
        self.assertEqual(self.client.post(reverse("session-action", args=[session.pk, "pause"])).status_code, 404)

    def test_session_form_lists_all_locations_and_allows_selecting_them(self):
        first_location = Location.objects.create(name="Home")
        second_location = Location.objects.create(name="Private club")
        third_location = Location.objects.create(name="Casino")
        response = self.client.get(reverse("session-create"))
        for location in (first_location, second_location, third_location):
            self.assertContains(response, location.name)
        response = self.client.post(reverse("session-create"), {
            "game": "No-Limit Hold'em", "buy_in": "100.00",
            "stakes": "$1/$2", "location": str(second_location.pk), "notes": "",
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(PokerSession.objects.get(user=self.user).location, second_location)

    def test_dashboard_and_session_list_include_session_edit_links(self):
        session = PokerSession.objects.create(user=self.user)
        edit_url = reverse("session-update", args=[session.pk])

        dashboard = self.client.get(reverse("dashboard"))
        self.assertContains(dashboard, f'href="{edit_url}"')
        self.assertContains(dashboard, f'aria-label="Edit session #{session.pk}"')

        session_list = self.client.get(reverse("session-list"))
        self.assertContains(session_list, f'href="{edit_url}"')

    def test_session_logbook_shows_overall_win_rate_and_hourly_rate(self):
        for index in range(21):
            buy_in = 100
            cash_out = 110 if index < 11 else 95
            PokerSession.objects.create(
                user=self.user,
                status=PokerSession.FINISHED,
                buy_in=buy_in,
                cash_out=cash_out,
                accumulated_seconds=3600,
                ended_at=timezone.now(),
            )
        PokerSession.objects.create(
            user=self.other,
            status=PokerSession.FINISHED,
            buy_in=100,
            cash_out=1100,
            accumulated_seconds=3600,
            ended_at=timezone.now(),
        )

        response = self.client.get(reverse("session-list"))
        self.assertEqual(response.context["paginator"].count, 21)
        self.assertEqual(response.context["profit"], Decimal("60"))
        self.assertEqual(response.context["hourly_rate"], Decimal("2.86"))
        self.assertContains(response, "AMOUNT WON/LOST OVERALL")
        self.assertNotContains(response, "OVERALL WIN RATE")
        self.assertContains(response, "+$60.00")
        self.assertContains(response, "$2.86")

    def test_month_statistics_filter_sessions_by_selected_month(self):
        jan = timezone.now().replace(year=2025, month=1, day=15)
        feb = timezone.now().replace(year=2025, month=2, day=15)
        matching = PokerSession.objects.create(
            user=self.user, status=PokerSession.FINISHED, buy_in=100, cash_out=150,
            started_at=jan, accumulated_seconds=3600, ended_at=jan + timedelta(hours=1),
        )
        PokerSession.objects.create(
            user=self.user, status=PokerSession.FINISHED, buy_in=100, cash_out=80,
            started_at=feb, accumulated_seconds=3600, ended_at=feb + timedelta(hours=1),
        )
        response = self.client.get(reverse("statistics-month"), {"selection": "2025-01"})
        self.assertEqual(list(response.context["sessions"]), [matching])
        self.assertEqual(response.context["profit"], Decimal("50"))
        self.assertContains(response, "January 2025")

    def test_year_statistics_filter_sessions_to_signed_in_user(self):
        session_date = timezone.now().replace(year=2024, month=6, day=15)
        matching = PokerSession.objects.create(
            user=self.user, status=PokerSession.FINISHED, buy_in=100, cash_out=120,
            started_at=session_date, accumulated_seconds=7200, ended_at=session_date + timedelta(hours=2),
        )
        PokerSession.objects.create(
            user=self.other, status=PokerSession.FINISHED, buy_in=100, cash_out=900,
            started_at=session_date, accumulated_seconds=3600, ended_at=session_date + timedelta(hours=1),
        )
        response = self.client.get(reverse("statistics-year"), {"selection": "2024"})
        self.assertEqual(list(response.context["sessions"]), [matching])
        self.assertEqual(response.context["hourly_rate"], Decimal("10.00"))

    def test_limit_statistics_filter_by_selected_stakes(self):
        matching = PokerSession.objects.create(
            user=self.user, status=PokerSession.FINISHED, stakes="$1/$2",
            buy_in=100, cash_out=120, accumulated_seconds=3600,
        )
        PokerSession.objects.create(
            user=self.user, status=PokerSession.FINISHED, stakes="$2/$5",
            buy_in=100, cash_out=500, accumulated_seconds=3600,
        )
        response = self.client.get(reverse("statistics-limit"), {"selection": "$1/$2"})
        self.assertEqual(list(response.context["sessions"]), [matching])
        self.assertEqual(response.context["profit"], Decimal("20"))

    def test_dashboard_profit(self):
        PokerSession.objects.create(
            user=self.user, status=PokerSession.FINISHED, buy_in=200, cash_out=350,
            accumulated_seconds=3600, ended_at=timezone.now(),
        )
        PokerSession.objects.create(
            user=self.user, status=PokerSession.FINISHED, buy_in=200, cash_out=130,
            accumulated_seconds=7200, ended_at=timezone.now(),
        )
        response = self.client.get(reverse("dashboard"))
        self.assertEqual(response.context["profit"], Decimal("80"))
        self.assertEqual(response.context["hourly_rate"], Decimal("26.67"))
        self.assertEqual(response.context["wins"], 1)
        self.assertEqual(response.context["losses"], 1)

    def test_dashboard_shows_net_profit_for_current_month(self):
        current_month = timezone.localtime().date()
        this_month = timezone.make_aware(
            timezone.datetime(current_month.year, current_month.month, 10, 12, 0)
        )
        previous_month = this_month - timedelta(days=40)
        PokerSession.objects.create(
            user=self.user, status=PokerSession.FINISHED, buy_in=100, cash_out=160,
            started_at=this_month, ended_at=this_month + timedelta(hours=1),
        )
        PokerSession.objects.create(
            user=self.user, status=PokerSession.FINISHED, buy_in=100, cash_out=180,
            started_at=previous_month, ended_at=previous_month + timedelta(hours=1),
        )
        PokerSession.objects.create(
            user=self.other, status=PokerSession.FINISHED, buy_in=100, cash_out=1000,
            started_at=this_month, ended_at=this_month + timedelta(hours=1),
        )

        response = self.client.get(reverse("dashboard"))
        self.assertEqual(response.context["won_this_month"], Decimal("60"))
        self.assertNotIn("win_rate", response.context)
        self.assertContains(response, "Amount won/lost this month")
        self.assertContains(response, "$60.00")
        self.assertNotContains(response, "WIN RATE")

    def test_dashboard_hourly_rate_is_zero_when_sessions_have_no_duration(self):
        PokerSession.objects.create(
            user=self.user, status=PokerSession.FINISHED, buy_in=100, cash_out=150,
            ended_at=timezone.now(),
        )
        response = self.client.get(reverse("dashboard"))
        self.assertEqual(response.context["hourly_rate"], Decimal("0.00"))

    def test_dashboard_hourly_rate_scales_short_session_duration(self):
        PokerSession.objects.create(
            user=self.user, status=PokerSession.FINISHED, buy_in=500, cash_out=600,
            accumulated_seconds=92, ended_at=timezone.now(),
        )
        response = self.client.get(reverse("dashboard"))
        self.assertEqual(response.context["hourly_rate"], Decimal("3913.04"))


class LocationAddressTests(TestCase):
    def test_map_urls_use_name_and_address(self):
        location = Location(name="Borgata", street_address="1 Borgata Way", address_line_2="Poker room",
                            city="Atlantic City", state="NJ", postal_code="08401")
        self.assertEqual(location.full_address, "1 Borgata Way, Poker room, Atlantic City, NJ 08401, United States")
        self.assertEqual(location.map_url, "https://www.google.com/maps/search/?api=1&query="
                         "Borgata%2C+1+Borgata+Way%2C+Atlantic+City%2C+NJ+08401%2C+United+States")
        self.assertTrue(location.map_embed_url.endswith("&output=embed"))

    def test_no_map_without_address(self):
        location = Location(name="Home game")
        self.assertFalse(location.has_address)
        self.assertEqual(location.map_url, "")
        self.assertEqual(location.map_embed_url, "")

    def test_new_location_address_requires_name(self):
        user = get_user_model().objects.create_user(username="carol", password="StrongPassword123!")
        self.client.force_login(user)
        response = self.client.post(reverse("session-create"), {
            "game": "No-Limit Hold'em", "buy_in": "100.00", "stakes": "$1/$2",
            "new_location_city": "Philadelphia", "notes": "",
        })
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Location.objects.exists())

    def test_split_address(self):
        cases = {
            "1 Borgata Way, Atlantic City, NJ": {"street_address": "1 Borgata Way", "city": "Atlantic City", "state": "NJ"},
            "Philadelphia": {"city": "Philadelphia"},
            "123 Main St": {"street_address": "123 Main St"},
            "1 Main St, Suite 4, Springfield, IL 62701": {
                "street_address": "1 Main St", "address_line_2": "Suite 4", "city": "Springfield",
                "state": "IL", "postal_code": "62701"},
            "": {},
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(split_address(text), expected)



class LocationStatisticsTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="dana", password="StrongPassword123!")
        self.other = get_user_model().objects.create_user(username="eve", password="StrongPassword123!")
        self.client.force_login(self.user)
        self.url = reverse("statistics-location")
        self.borgata = Location.objects.create(name="Borgata", street_address="1 Borgata Way",
                                               city="Atlantic City", state="NJ")
        self.parx = Location.objects.create(name="Parx", city="Bensalem", state="PA")

    def finished(self, location, buy_in, cash_out, user=None, hours=2):
        return PokerSession.objects.create(
            user=user or self.user, location=location, status=PokerSession.FINISHED, resumed_at=None,
            buy_in=Decimal(buy_in), cash_out=Decimal(cash_out), accumulated_seconds=hours * 3600,
        )

    def test_requires_login(self):
        self.client.logout()
        self.assertEqual(self.client.get(self.url).status_code, 302)

    def test_ranking_by_location(self):
        self.finished(self.borgata, "200", "500")
        self.finished(self.borgata, "200", "100")
        self.finished(self.borgata, "200", "200")
        self.finished(self.parx, "300", "100")
        self.finished(None, "100", "150")
        PokerSession.objects.create(user=self.user, location=self.parx)  # still running: not counted
        self.finished(self.parx, "100", "9000", user=self.other)  # someone else's session

        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        rows = response.context["rows"]
        self.assertEqual([row["key"] for row in rows], [self.borgata.pk, "none", self.parx.pk])
        borgata, no_location, parx = rows
        self.assertEqual((borgata["wins"], borgata["losses"], borgata["break_even"]), (1, 1, 1))
        self.assertEqual(borgata["profit"], Decimal("200"))
        self.assertEqual(borgata["win_rate"], 33.3)
        self.assertEqual(borgata["hourly_rate"], Decimal("33.33"))
        self.assertEqual(no_location["location"], None)
        self.assertEqual((parx["completed_count"], parx["profit"]), (1, Decimal("-200")))
        self.assertContains(response, "No location")
        self.assertContains(response, "Atlantic City, NJ")

    def test_location_detail(self):
        self.finished(self.borgata, "200", "500")
        self.finished(self.borgata, "200", "100")
        response = self.client.get(self.url, {"location": self.borgata.pk})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["location"], self.borgata)
        self.assertEqual(response.context["profit"], Decimal("200"))
        self.assertEqual(response.context["wins"], 1)
        self.assertEqual(response.context["sessions"].count(), 2)
        self.assertContains(response, "Open in Maps")

    def test_sessions_without_location_detail(self):
        self.finished(None, "100", "50")
        response = self.client.get(self.url, {"location": "none"})
        self.assertEqual(response.context["losses"], 1)
        self.assertContains(response, "Sessions recorded without a location.")

    def test_invalid_or_unplayed_location(self):
        self.finished(self.parx, "100", "200", user=self.other)
        for value in (str(self.parx.pk), "999", "abc"):
            with self.subTest(value=value):
                response = self.client.get(self.url, {"location": value})
                self.assertTrue(response.context["invalid_selection"])
                self.assertContains(response, "That location isn't available.")

    def test_empty_state(self):
        response = self.client.get(self.url)
        self.assertEqual(response.context["rows"], [])
        self.assertContains(response, "Finish a session at a location")

    def test_tab_on_other_statistics_pages(self):
        response = self.client.get(reverse("statistics-month"))
        self.assertContains(response, f'href="{self.url}"')


class NavigationTests(TestCase):
    def setUp(self):
        user = get_user_model().objects.create_user(username="frank", password="StrongPassword123!")
        self.client.force_login(user)

    def test_single_filtered_stats_link_reaches_every_statistics_page(self):
        stats_pages = ["statistics-month", "statistics-year", "statistics-limit", "statistics-location"]
        for name in stats_pages:
            with self.subTest(page=name):
                html = self.client.get(reverse(name)).content.decode()
                nav = html[html.index('<nav class="nav"'):html.index("</nav>")]
                self.assertIn(f'href="{reverse("statistics-month")}" aria-current="page"><span aria-hidden="true">◷</span><span>Filtered stats</span>', nav)
                for other in stats_pages:
                    self.assertIn(f'href="{reverse(other)}"', html)  # reachable via the tabs

    def test_filtered_stats_not_highlighted_elsewhere(self):
        html = self.client.get(reverse("dashboard")).content.decode()
        nav = html[html.index('<nav class="nav"'):html.index("</nav>")]
        self.assertIn("Filtered stats", nav)
        self.assertNotIn(">Month<", nav)
        self.assertEqual(nav.count('aria-current="page"'), 1)
