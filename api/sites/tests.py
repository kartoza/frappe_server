"""Tests for Job state transitions, Site hostname normalization, nginx
config generation, and app-fetch/install flows."""
import io
import json
import os
import tarfile
import tempfile
import uuid
import zipfile
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from rest_framework import status
from rest_framework.test import APITestCase

import pymysql

from benches.models import Bench, Server

from . import nginx_config, sql_playground
from .models import App, Job, Site


class JobStateTests(TestCase):
    def setUp(self):
        server = Server.objects.create(name='localhost')
        self.bench = Bench.objects.create(
            server=server, name='bench-a', frappe_branch='version-15'
        )

    def test_append_log_accumulates_and_truncates(self):
        job = Job.objects.create(job_type=Job.TYPE_SITE_CREATE, bench=self.bench)
        with self.settings(JOB_LOG_MAX_CHARS=10):
            job.append_log('0123456789abcdef')
        job.refresh_from_db()
        self.assertEqual(len(job.logs), 10)
        self.assertTrue(job.log_truncated)

    def test_mark_running_then_success_transitions(self):
        job = Job.objects.create(job_type=Job.TYPE_SITE_CREATE, bench=self.bench)
        job.mark_running()
        self.assertEqual(job.status, Job.STATUS_RUNNING)

        job.mark_success()
        self.assertEqual(job.status, Job.STATUS_SUCCESS)
        self.assertIsNotNone(job.finished_at)

    def test_mark_failed_records_message_in_logs(self):
        job = Job.objects.create(job_type=Job.TYPE_SITE_CREATE, bench=self.bench)
        job.mark_failed('boom')
        self.assertEqual(job.status, Job.STATUS_FAILED)
        self.assertIn('boom', job.logs)


class SiteHostnameTests(TestCase):
    def test_hostname_is_lowercased_on_save(self):
        server = Server.objects.create(name='localhost')
        bench = Bench.objects.create(server=server, name='bench-a')
        site = Site.objects.create(
            bench=bench, hostname='MySite.Localhost', admin_password='pw'
        )
        self.assertEqual(site.hostname, 'mysite.localhost')


class HostnameDomainWhitelistTests(APITestCase):
    """A site's hostname must fall under settings.ALLOWED_SITE_DOMAINS —
    nginx itself will route any hostname it's given, so this is the real
    gate against creating a site under a domain the operator doesn't
    control."""

    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username='admin@example.com', password='s3cret-pass', is_staff=True
        )
        self.client.force_authenticate(user=self.user)
        server = Server.objects.create(name='localhost')
        self.bench = Bench.objects.create(
            server=server, name='bench-a', frappe_branch='version-15',
            status=Bench.STATUS_ACTIVE,
        )

    def test_is_hostname_allowed_matches_exact_domain_and_subdomains(self):
        from django.test import override_settings
        from .models import is_hostname_allowed

        with override_settings(ALLOWED_SITE_DOMAINS=['localhost', 'kartoza.com']):
            self.assertTrue(is_hostname_allowed('localhost'))
            self.assertTrue(is_hostname_allowed('demo.localhost'))
            self.assertTrue(is_hostname_allowed('kartoza.com'))
            self.assertTrue(is_hostname_allowed('shop.kartoza.com'))
            self.assertFalse(is_hostname_allowed('evil.com'))
            # A domain that merely *ends with* an allowed one isn't a
            # subdomain of it — "notkartoza.com" must not sneak through.
            self.assertFalse(is_hostname_allowed('notkartoza.com'))

    @patch('sites.views.create_site_task')
    def test_create_site_rejects_hostname_outside_whitelist(self, mock_task):
        response = self.client.post('/api/sites/', {
            'bench': self.bench.id,
            'hostname': 'demo.evil.com',
            'admin_password': 'pw',
        })
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        mock_task.delay.assert_not_called()
        self.assertFalse(Site.objects.filter(hostname='demo.evil.com').exists())

    @patch('sites.views.create_site_task')
    def test_create_site_accepts_hostname_under_default_localhost_domain(self, mock_task):
        response = self.client.post('/api/sites/', {
            'bench': self.bench.id,
            'hostname': 'demo.localhost',
            'admin_password': 'pw',
        })
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        mock_task.delay.assert_called_once()

    @patch('sites.views.create_site_task')
    def test_create_site_accepts_custom_domain_once_whitelisted(self, mock_task):
        from django.test import override_settings

        with override_settings(ALLOWED_SITE_DOMAINS=['localhost', 'kartoza.com']):
            response = self.client.post('/api/sites/', {
                'bench': self.bench.id,
                'hostname': 'shop.kartoza.com',
                'admin_password': 'pw',
            })
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        mock_task.delay.assert_called_once()

    def test_allowed_domains_endpoint_reflects_setting(self):
        from django.test import override_settings

        with override_settings(ALLOWED_SITE_DOMAINS=['localhost', 'kartoza.com']):
            response = self.client.get('/api/sites/allowed-domains/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['domains'], ['localhost', 'kartoza.com'])


class SiteDomainModelTests(TestCase):
    """A site's own hostname is fixed and never gets a SiteDomain row —
    these just cover the computed helpers built on top of that."""

    def setUp(self):
        server = Server.objects.create(name='localhost')
        bench = Bench.objects.create(server=server, name='bench-a', status=Bench.STATUS_ACTIVE)
        self.site = Site.objects.create(
            bench=bench, hostname='demo.localhost', admin_password='pw', status=Site.STATUS_ACTIVE,
        )

    def test_default_domain_is_hostname_with_no_custom_domains(self):
        self.assertEqual(self.site.default_domain, 'demo.localhost')
        self.assertEqual(self.site.all_domains, ['demo.localhost'])

    def test_default_domain_switches_once_a_custom_domain_is_marked_default(self):
        from .models import SiteDomain

        SiteDomain.objects.create(site=self.site, domain='shop.kartoza.com', is_default=True)
        self.assertEqual(self.site.default_domain, 'shop.kartoza.com')
        self.assertEqual(set(self.site.all_domains), {'demo.localhost', 'shop.kartoza.com'})

    def test_only_one_default_domain_allowed_per_site(self):
        from django.db import IntegrityError
        from .models import SiteDomain

        SiteDomain.objects.create(site=self.site, domain='a.kartoza.com', is_default=True)
        with self.assertRaises(IntegrityError):
            SiteDomain.objects.create(site=self.site, domain='b.kartoza.com', is_default=True)


class SiteDomainViewSetTests(APITestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username='admin@example.com', password='s3cret-pass', is_staff=True
        )
        self.client.force_authenticate(user=self.user)
        server = Server.objects.create(name='localhost')
        self.bench = Bench.objects.create(server=server, name='bench-a', status=Bench.STATUS_ACTIVE)
        self.site = Site.objects.create(
            bench=self.bench, hostname='demo.localhost', admin_password='pw', status=Site.STATUS_ACTIVE,
        )

    @patch('sites.views.regenerate_nginx_task')
    def test_add_domain_outside_whitelist_is_rejected(self, mock_task):
        response = self.client.post('/api/site-domains/', {'site': self.site.id, 'domain': 'shop.evil.com'})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        mock_task.delay.assert_not_called()

    @patch('sites.views.regenerate_nginx_task')
    def test_add_domain_already_a_site_hostname_is_rejected(self, mock_task):
        Site.objects.create(bench=self.bench, hostname='other.localhost', admin_password='pw')
        response = self.client.post('/api/site-domains/', {'site': self.site.id, 'domain': 'other.localhost'})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        mock_task.delay.assert_not_called()

    @patch('sites.views.renew_ssl_certs_task')
    @patch('sites.views.regenerate_nginx_task')
    def test_add_domain_succeeds_and_triggers_nginx_regen(self, mock_regen, mock_ssl):
        with override_settings(ALLOWED_SITE_DOMAINS=['localhost', 'kartoza.com']):
            response = self.client.post(
                '/api/site-domains/', {'site': self.site.id, 'domain': 'shop.kartoza.com'}
            )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(self.site.domains.count(), 1)
        mock_regen.delay.assert_called_once()
        mock_ssl.delay.assert_not_called()  # SSL_ENABLED defaults off

    @patch('sites.views.regenerate_nginx_task')
    def test_list_domains_filtered_by_site(self, mock_task):
        from .models import SiteDomain

        other_site = Site.objects.create(bench=self.bench, hostname='other.localhost', admin_password='pw')
        SiteDomain.objects.create(site=self.site, domain='a.kartoza.com')
        SiteDomain.objects.create(site=other_site, domain='b.kartoza.com')

        response = self.client.get(f'/api/site-domains/?site={self.site.id}')
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]['domain'], 'a.kartoza.com')

    @patch('sites.views.regenerate_nginx_task')
    def test_remove_domain(self, mock_task):
        from .models import SiteDomain

        domain = SiteDomain.objects.create(site=self.site, domain='a.kartoza.com')
        response = self.client.delete(f'/api/site-domains/{domain.id}/')
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(SiteDomain.objects.filter(id=domain.id).exists())
        mock_task.delay.assert_called_once()


class SetDefaultDomainTests(APITestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username='admin@example.com', password='s3cret-pass', is_staff=True
        )
        self.client.force_authenticate(user=self.user)
        server = Server.objects.create(name='localhost')
        self.bench = Bench.objects.create(server=server, name='bench-a', status=Bench.STATUS_ACTIVE)
        self.site = Site.objects.create(
            bench=self.bench, hostname='demo.localhost', admin_password='pw', status=Site.STATUS_ACTIVE,
        )

    @patch('sites.views.regenerate_nginx_task')
    def test_set_a_custom_domain_as_default(self, mock_task):
        from .models import SiteDomain

        domain = SiteDomain.objects.create(site=self.site, domain='shop.kartoza.com')
        response = self.client.post(
            f'/api/sites/{self.site.id}/set-default-domain/', {'domain': 'shop.kartoza.com'}
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        domain.refresh_from_db()
        self.assertTrue(domain.is_default)
        self.assertEqual(response.data['default_domain'], 'shop.kartoza.com')
        mock_task.delay.assert_called_once()

    @patch('sites.views.regenerate_nginx_task')
    def test_switching_default_clears_the_previous_one(self, mock_task):
        from .models import SiteDomain

        first = SiteDomain.objects.create(site=self.site, domain='a.kartoza.com', is_default=True)
        second = SiteDomain.objects.create(site=self.site, domain='b.kartoza.com')

        response = self.client.post(f'/api/sites/{self.site.id}/set-default-domain/', {'domain': 'b.kartoza.com'})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertFalse(first.is_default)
        self.assertTrue(second.is_default)

    @patch('sites.views.regenerate_nginx_task')
    def test_set_back_to_the_sites_own_hostname(self, mock_task):
        from .models import SiteDomain

        custom = SiteDomain.objects.create(site=self.site, domain='shop.kartoza.com', is_default=True)
        response = self.client.post(
            f'/api/sites/{self.site.id}/set-default-domain/', {'domain': 'demo.localhost'}
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        custom.refresh_from_db()
        self.assertFalse(custom.is_default)
        self.assertEqual(response.data['default_domain'], 'demo.localhost')

    def test_rejects_a_domain_not_on_this_site(self):
        response = self.client.post(
            f'/api/sites/{self.site.id}/set-default-domain/', {'domain': 'not-mine.kartoza.com'}
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


class NginxConfigTests(TestCase):
    def setUp(self):
        server = Server.objects.create(name='localhost')
        self.bench = Bench.objects.create(
            server=server, name='bench-a', frappe_branch='version-15'
        )

    @patch('sites.nginx_config.run_command', return_value=(0, ''))
    @patch('sites.nginx_config._redis_client')
    def test_regenerate_writes_one_block_per_active_site(
        self, mock_redis_client, mock_run_command
    ):
        active_site = Site.objects.create(
            bench=self.bench,
            hostname='active.localhost',
            admin_password='pw',
            status=Site.STATUS_ACTIVE,
        )
        Site.objects.create(
            bench=self.bench,
            hostname='pending.localhost',
            admin_password='pw',
            status=Site.STATUS_CREATING,
        )

        mock_lock = MagicMock()
        mock_lock.__enter__ = MagicMock(return_value=None)
        mock_lock.__exit__ = MagicMock(return_value=False)
        mock_redis_client.return_value.lock.return_value = mock_lock

        with tempfile.TemporaryDirectory() as tmp_dir:
            with self.settings(NGINX_CONF_DIR=tmp_dir):
                nginx_config.regenerate_and_reload()
                with open(f'{tmp_dir}/sites.conf', encoding='utf-8') as handle:
                    content = handle.read()

        self.assertIn(active_site.hostname, content)
        self.assertNotIn('pending.localhost', content)
        self.assertIn(self.bench.container_name, content)
        mock_run_command.assert_called_once()

    @patch('sites.ssl.has_cert', return_value=True)
    @patch('sites.nginx_config.run_command', return_value=(0, ''))
    @patch('sites.nginx_config._redis_client')
    def test_regenerate_renders_https_block_once_a_cert_exists(
        self, mock_redis_client, mock_run_command, mock_has_cert
    ):
        Site.objects.create(
            bench=self.bench, hostname='shop.kartoza.com', admin_password='pw',
            status=Site.STATUS_ACTIVE,
        )
        mock_lock = MagicMock()
        mock_lock.__enter__ = MagicMock(return_value=None)
        mock_lock.__exit__ = MagicMock(return_value=False)
        mock_redis_client.return_value.lock.return_value = mock_lock

        with tempfile.TemporaryDirectory() as tmp_dir:
            with self.settings(NGINX_CONF_DIR=tmp_dir, SSL_ENABLED=True):
                nginx_config.regenerate_and_reload()
                with open(f'{tmp_dir}/sites.conf', encoding='utf-8') as handle:
                    content = handle.read()

        self.assertIn('listen 443 ssl', content)
        self.assertIn('ssl_certificate', content)
        self.assertIn('return 301 https', content)
        # Braces must have fully resolved — a leftover "{{"/"}}" would mean
        # an un-.format()'d template fragment slipped into the real file.
        self.assertNotIn('{{', content)
        self.assertNotIn('}}', content)

    @patch('sites.ssl.has_cert', return_value=True)
    @patch('sites.nginx_config.run_command', return_value=(0, ''))
    @patch('sites.nginx_config._redis_client')
    def test_regenerate_skips_https_block_when_ssl_disabled_even_with_a_cert(
        self, mock_redis_client, mock_run_command, mock_has_cert
    ):
        """A cert could be sitting on disk from before SSL_ENABLED was
        turned off again — must not serve HTTPS in that case."""
        Site.objects.create(
            bench=self.bench, hostname='shop.kartoza.com', admin_password='pw',
            status=Site.STATUS_ACTIVE,
        )
        mock_lock = MagicMock()
        mock_lock.__enter__ = MagicMock(return_value=None)
        mock_lock.__exit__ = MagicMock(return_value=False)
        mock_redis_client.return_value.lock.return_value = mock_lock

        with tempfile.TemporaryDirectory() as tmp_dir:
            with self.settings(NGINX_CONF_DIR=tmp_dir, SSL_ENABLED=False):
                nginx_config.regenerate_and_reload()
                with open(f'{tmp_dir}/sites.conf', encoding='utf-8') as handle:
                    content = handle.read()

        self.assertNotIn('listen 443', content)
        self.assertIn('listen 80', content)

    @patch('sites.nginx_config.run_command', return_value=(0, ''))
    @patch('sites.nginx_config._redis_client')
    def test_every_site_gets_the_acme_challenge_location(self, mock_redis_client, mock_run_command):
        Site.objects.create(
            bench=self.bench, hostname='demo.localhost', admin_password='pw',
            status=Site.STATUS_ACTIVE,
        )
        mock_lock = MagicMock()
        mock_lock.__enter__ = MagicMock(return_value=None)
        mock_lock.__exit__ = MagicMock(return_value=False)
        mock_redis_client.return_value.lock.return_value = mock_lock

        with tempfile.TemporaryDirectory() as tmp_dir:
            with self.settings(NGINX_CONF_DIR=tmp_dir):
                nginx_config.regenerate_and_reload()
                with open(f'{tmp_dir}/sites.conf', encoding='utf-8') as handle:
                    content = handle.read()

        self.assertIn('/.well-known/acme-challenge/', content)

    @patch('sites.nginx_config.run_command', return_value=(0, ''))
    @patch('sites.nginx_config._redis_client')
    def test_every_domain_gets_x_frappe_site_name_set_to_the_real_hostname(
        self, mock_redis_client, mock_run_command
    ):
        """Frappe resolves which site to serve from this header before
        falling back to the Host header — without it, a request arriving
        on a custom domain would look for a site folder matching that
        domain, which doesn't exist."""
        from .models import SiteDomain

        site = Site.objects.create(
            bench=self.bench, hostname='demo.localhost', admin_password='pw',
            status=Site.STATUS_ACTIVE,
        )
        SiteDomain.objects.create(site=site, domain='shop.kartoza.com')
        mock_lock = MagicMock()
        mock_lock.__enter__ = MagicMock(return_value=None)
        mock_lock.__exit__ = MagicMock(return_value=False)
        mock_redis_client.return_value.lock.return_value = mock_lock

        with tempfile.TemporaryDirectory() as tmp_dir:
            with self.settings(NGINX_CONF_DIR=tmp_dir):
                nginx_config.regenerate_and_reload()
                with open(f'{tmp_dir}/sites.conf', encoding='utf-8') as handle:
                    content = handle.read()

        # The custom domain isn't default yet, so it should redirect to
        # the real hostname rather than carry its own proxy block.
        self.assertIn('server_name demo.localhost', content)
        self.assertIn('server_name shop.kartoza.com', content)
        self.assertIn('X-Frappe-Site-Name demo.localhost', content)
        self.assertIn('return 301 http://demo.localhost$request_uri', content)

    @patch('sites.nginx_config.run_command', return_value=(0, ''))
    @patch('sites.nginx_config._redis_client')
    def test_non_default_domain_redirects_to_the_default_one(self, mock_redis_client, mock_run_command):
        from .models import SiteDomain

        site = Site.objects.create(
            bench=self.bench, hostname='demo.localhost', admin_password='pw',
            status=Site.STATUS_ACTIVE,
        )
        SiteDomain.objects.create(site=site, domain='shop.kartoza.com', is_default=True)
        mock_lock = MagicMock()
        mock_lock.__enter__ = MagicMock(return_value=None)
        mock_lock.__exit__ = MagicMock(return_value=False)
        mock_redis_client.return_value.lock.return_value = mock_lock

        with tempfile.TemporaryDirectory() as tmp_dir:
            with self.settings(NGINX_CONF_DIR=tmp_dir):
                nginx_config.regenerate_and_reload()
                with open(f'{tmp_dir}/sites.conf', encoding='utf-8') as handle:
                    content = handle.read()

        # Now the *custom* domain carries the real proxy block, and the
        # site's own original hostname redirects there instead.
        self.assertIn('return 301 http://shop.kartoza.com$request_uri', content)
        self.assertIn('X-Frappe-Site-Name demo.localhost', content)
        self.assertNotIn('{{', content)
        self.assertNotIn('}}', content)


class SslAutomationTests(TestCase):
    """certbot itself is never actually invoked in tests — these mock the
    subprocess boundary and check the orchestration logic around it:
    which hostnames are eligible, and that a batch run reloads nginx."""

    def setUp(self):
        server = Server.objects.create(name='localhost')
        self.bench = Bench.objects.create(server=server, name='bench-a')

    def test_eligible_hostnames_excludes_localhost_sites(self):
        from . import ssl as ssl_module

        Site.objects.create(
            bench=self.bench, hostname='demo.localhost', admin_password='pw',
            status=Site.STATUS_ACTIVE,
        )
        Site.objects.create(
            bench=self.bench, hostname='shop.kartoza.com', admin_password='pw',
            status=Site.STATUS_ACTIVE,
        )
        Site.objects.create(
            bench=self.bench, hostname='not-active.kartoza.com', admin_password='pw',
            status=Site.STATUS_CREATING,
        )

        hostnames = ssl_module.eligible_hostnames()
        self.assertEqual(hostnames, ['shop.kartoza.com'])

    def test_eligible_hostnames_includes_custom_domains(self):
        from . import ssl as ssl_module
        from .models import SiteDomain

        site = Site.objects.create(
            bench=self.bench, hostname='demo.localhost', admin_password='pw',
            status=Site.STATUS_ACTIVE,
        )
        SiteDomain.objects.create(site=site, domain='shop.kartoza.com', is_default=True)

        hostnames = ssl_module.eligible_hostnames()
        # "demo.localhost" itself is still excluded (SSL_EXCLUDED_DOMAINS),
        # but the custom domain layered on top of it needs its own cert
        # regardless of which one is marked default.
        self.assertEqual(hostnames, ['shop.kartoza.com'])

    @patch('sites.ssl.subprocess.run')
    def test_issue_or_renew_reports_success(self, mock_run):
        from . import ssl as ssl_module

        mock_run.return_value = MagicMock(returncode=0, stdout='Congratulations!', stderr='')
        with self.settings(SSL_ENABLED=True):
            success, output = ssl_module.issue_or_renew('shop.kartoza.com')

        self.assertTrue(success)
        self.assertIn('Congratulations', output)
        command = mock_run.call_args[0][0]
        self.assertIn('certbot', command)
        self.assertIn('shop.kartoza.com', command)

    @patch('sites.ssl.subprocess.run')
    def test_issue_or_renew_reports_failure_without_raising(self, mock_run):
        from . import ssl as ssl_module

        mock_run.return_value = MagicMock(returncode=1, stdout='', stderr='DNS problem')
        success, output = ssl_module.issue_or_renew('shop.kartoza.com')

        self.assertFalse(success)
        self.assertIn('DNS problem', output)

    @patch('sites.ssl.subprocess.run', side_effect=TimeoutError('timed out'))
    def test_issue_or_renew_never_raises_even_on_subprocess_error(self, mock_run):
        from . import ssl as ssl_module

        success, output = ssl_module.issue_or_renew('shop.kartoza.com')
        self.assertFalse(success)

    def test_run_for_all_eligible_sites_is_a_noop_when_ssl_disabled(self):
        from . import ssl as ssl_module

        Site.objects.create(
            bench=self.bench, hostname='shop.kartoza.com', admin_password='pw',
            status=Site.STATUS_ACTIVE,
        )
        with self.settings(SSL_ENABLED=False):
            results = ssl_module.run_for_all_eligible_sites()
        self.assertEqual(results, {})

    @patch('sites.nginx_config.regenerate_and_reload')
    @patch('sites.ssl.issue_or_renew', return_value=(True, 'ok'))
    def test_run_for_all_eligible_sites_reloads_nginx_when_something_was_attempted(
        self, mock_issue, mock_reload
    ):
        from . import ssl as ssl_module

        Site.objects.create(
            bench=self.bench, hostname='shop.kartoza.com', admin_password='pw',
            status=Site.STATUS_ACTIVE,
        )
        with self.settings(SSL_ENABLED=True):
            results = ssl_module.run_for_all_eligible_sites()

        self.assertEqual(results, {'shop.kartoza.com': (True, 'ok')})
        mock_reload.assert_called_once()

    @patch('sites.nginx_config.regenerate_and_reload')
    def test_run_for_all_eligible_sites_skips_reload_when_nothing_eligible(self, mock_reload):
        from . import ssl as ssl_module

        Site.objects.create(
            bench=self.bench, hostname='demo.localhost', admin_password='pw',
            status=Site.STATUS_ACTIVE,
        )
        with self.settings(SSL_ENABLED=True):
            results = ssl_module.run_for_all_eligible_sites()

        self.assertEqual(results, {})
        mock_reload.assert_not_called()


class IssueSslCertsCommandTests(TestCase):
    def setUp(self):
        server = Server.objects.create(name='localhost')
        self.bench = Bench.objects.create(server=server, name='bench-a')

    def test_command_skips_cleanly_when_ssl_disabled(self):
        from io import StringIO
        from django.core.management import call_command

        out = StringIO()
        with self.settings(SSL_ENABLED=False):
            call_command('issue_ssl_certs', stdout=out)
        self.assertIn('skipping', out.getvalue())

    @patch('sites.ssl.issue_or_renew', return_value=(True, 'ok'))
    @patch('sites.nginx_config.regenerate_and_reload')
    def test_command_reports_results_per_hostname(self, mock_reload, mock_issue):
        from io import StringIO
        from django.core.management import call_command

        Site.objects.create(
            bench=self.bench, hostname='shop.kartoza.com', admin_password='pw',
            status=Site.STATUS_ACTIVE,
        )
        out = StringIO()
        with self.settings(SSL_ENABLED=True):
            call_command('issue_ssl_certs', stdout=out)
        self.assertIn('shop.kartoza.com: ok', out.getvalue())


class RenewSslCertsTaskTests(TestCase):
    @patch('sites.ssl.run_for_all_eligible_sites')
    def test_task_delegates_to_shared_ssl_logic(self, mock_run):
        from .tasks import renew_ssl_certs_task

        renew_ssl_certs_task()
        mock_run.assert_called_once()


class SiteAppMustBeFetchedTests(APITestCase):
    """An app can only be installed on a site if it's already fetched onto
    that site's bench — no auto-fetch, no branch-matching restriction.
    Branch choice happens explicitly when fetching onto the bench."""

    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username='admin@example.com', password='s3cret-pass', is_staff=True
        )
        self.client.force_authenticate(user=self.user)
        server = Server.objects.create(name='localhost')
        self.bench = Bench.objects.create(
            server=server, name='bench-a', frappe_branch='version-14',
            status=Bench.STATUS_ACTIVE,
        )
        self.app = App.objects.create(name='erpnext', repo_url='https://example.com/erpnext')

    @patch('sites.views.create_site_task')
    def test_create_site_rejects_app_not_fetched_on_bench(self, mock_task):
        response = self.client.post('/api/sites/', {
            'bench': self.bench.id,
            'hostname': 'demo.localhost',
            'admin_password': 'pw',
            'app_ids': [self.app.id],
        })
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        mock_task.delay.assert_not_called()
        self.assertFalse(Site.objects.filter(hostname='demo.localhost').exists())

    @patch('sites.views.create_site_task')
    def test_create_site_accepts_app_already_fetched_on_bench(self, mock_task):
        from .models import BenchApp

        BenchApp.objects.create(bench=self.bench, app=self.app, branch='some-branch')

        response = self.client.post('/api/sites/', {
            'bench': self.bench.id,
            'hostname': 'demo.localhost',
            'admin_password': 'pw',
            'app_ids': [self.app.id],
        })
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        mock_task.delay.assert_called_once()

    @patch('sites.views.install_app_task')
    def test_install_app_action_rejects_app_not_fetched_on_bench(self, mock_task):
        site = Site.objects.create(
            bench=self.bench, hostname='demo.localhost', admin_password='pw',
            status=Site.STATUS_ACTIVE,
        )
        response = self.client.post(
            f'/api/sites/{site.id}/install-app/', {'app_id': self.app.id}
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        mock_task.delay.assert_not_called()

    @patch('sites.views.install_app_task')
    def test_install_app_action_accepts_app_already_fetched_on_bench(self, mock_task):
        from .models import BenchApp

        BenchApp.objects.create(bench=self.bench, app=self.app, branch='some-branch')
        site = Site.objects.create(
            bench=self.bench, hostname='demo.localhost', admin_password='pw',
            status=Site.STATUS_ACTIVE,
        )
        response = self.client.post(
            f'/api/sites/{site.id}/install-app/', {'app_id': self.app.id}
        )
        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        mock_task.delay.assert_called_once()


class DeleteSiteTests(APITestCase):
    """Deleting a site drops its database/files on the bench and removes
    it from nginx routing — this is async (a Job), not immediate."""

    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username='admin@example.com', password='s3cret-pass', is_staff=True
        )
        self.client.force_authenticate(user=self.user)
        server = Server.objects.create(name='localhost')
        self.bench = Bench.objects.create(
            server=server, name='bench-a', frappe_branch='version-15',
            status=Bench.STATUS_ACTIVE,
        )
        self.site = Site.objects.create(
            bench=self.bench, hostname='demo.localhost', admin_password='pw',
            status=Site.STATUS_ACTIVE,
        )

    @patch('sites.views.delete_site_task')
    def test_destroy_marks_deleting_and_enqueues_job(self, mock_task):
        response = self.client.delete(f'/api/sites/{self.site.id}/')

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        self.assertIn('job_id', response.data)
        mock_task.delay.assert_called_once()
        self.site.refresh_from_db()
        self.assertEqual(self.site.status, Site.STATUS_DELETING)
        job = Job.objects.get(pk=response.data['job_id'])
        self.assertEqual(job.job_type, Job.TYPE_SITE_DELETE)
        self.assertEqual(job.site, self.site)

    @patch('sites.tasks.nginx_config.regenerate_and_reload')
    @patch('sites.tasks.bench_exec.drop_site')
    def test_delete_site_task_drops_site_and_regenerates_nginx(self, mock_drop, mock_reload):
        from .tasks import delete_site_task

        job = Job.objects.create(job_type=Job.TYPE_SITE_DELETE, site=self.site, bench=self.bench)
        site_id = self.site.id

        delete_site_task(site_id, job.id)

        mock_drop.assert_called_once()
        mock_reload.assert_called_once()
        self.assertFalse(Site.objects.filter(pk=site_id).exists())
        job.refresh_from_db()
        self.assertEqual(job.status, Job.STATUS_SUCCESS)

    @patch('sites.tasks.nginx_config.regenerate_and_reload')
    @patch('sites.tasks.bench_exec.drop_site', side_effect=RuntimeError('drop-site failed'))
    def test_delete_site_task_marks_error_and_keeps_site_on_failure(self, mock_drop, mock_reload):
        from .tasks import delete_site_task

        job = Job.objects.create(job_type=Job.TYPE_SITE_DELETE, site=self.site, bench=self.bench)

        delete_site_task(self.site.id, job.id)

        self.assertTrue(Site.objects.filter(pk=self.site.id).exists())
        self.site.refresh_from_db()
        self.assertEqual(self.site.status, Site.STATUS_ERROR)
        self.assertEqual(self.site.error_message, 'drop-site failed')
        mock_reload.assert_not_called()
        job.refresh_from_db()
        self.assertEqual(job.status, Job.STATUS_FAILED)


class OrphanedJobReconciliationTests(TestCase):
    def test_running_jobs_are_marked_failed_queued_jobs_are_untouched(self):
        from platform_api.celery import reconcile_orphaned_jobs

        server = Server.objects.create(name='localhost')
        bench = Bench.objects.create(server=server, name='bench-a')
        running_job = Job.objects.create(job_type=Job.TYPE_APP_FETCH, bench=bench)
        running_job.mark_running()
        queued_job = Job.objects.create(job_type=Job.TYPE_APP_FETCH, bench=bench)

        reconcile_orphaned_jobs()

        running_job.refresh_from_db()
        queued_job.refresh_from_db()
        self.assertEqual(running_job.status, Job.STATUS_FAILED)
        self.assertIn('restarted', running_job.logs)
        self.assertEqual(queued_job.status, Job.STATUS_QUEUED)


class AppFetchRepairTests(TestCase):
    """A partially-fetched app (dir cloned, package not installed — e.g. a
    worker crash mid-``pip install``) must be detected and re-fetched, not
    mistaken for already being usable."""

    def setUp(self):
        server = Server.objects.create(name='localhost')
        self.bench = Bench.objects.create(
            server=server, name='bench-a', frappe_branch='version-15'
        )
        self.app = App.objects.create(name='erpnext', repo_url='https://example.com/erpnext')
        self.job = Job.objects.create(job_type=Job.TYPE_APP_FETCH, bench=self.bench)

    @patch('sites.bench_exec.exec_stream', return_value=0)
    @patch('sites.bench_exec.run_command', return_value=(1, ''))
    def test_get_app_refetches_with_overwrite_when_not_importable(
        self, mock_run_command, mock_exec_stream
    ):
        from . import bench_exec

        bench_exec.get_app(self.bench, self.app, 'version-15', self.job)

        mock_exec_stream.assert_called_once()
        command = mock_exec_stream.call_args[0][1]
        self.assertIn('--overwrite', command)
        self.assertIn('version-15', command)

    @patch('sites.bench_exec.exec_stream')
    def test_get_app_skips_when_already_fetched_at_same_branch_and_importable(
        self, mock_exec_stream
    ):
        from . import bench_exec
        from .models import BenchApp

        BenchApp.objects.create(bench=self.bench, app=self.app, branch='version-15')
        with patch('sites.bench_exec.run_command', return_value=(0, '')):
            bench_exec.get_app(self.bench, self.app, 'version-15', self.job)

        mock_exec_stream.assert_not_called()

    @patch('sites.bench_exec.exec_stream', return_value=0)
    def test_get_app_refetches_when_requested_branch_differs(self, mock_exec_stream):
        from . import bench_exec
        from .models import BenchApp

        BenchApp.objects.create(bench=self.bench, app=self.app, branch='version-14')
        with patch('sites.bench_exec.run_command', return_value=(0, '')):
            bench_exec.get_app(self.bench, self.app, 'version-15', self.job)

        mock_exec_stream.assert_called_once()
        command = mock_exec_stream.call_args[0][1]
        self.assertIn('version-15', command)


class AppActualNameResolutionTests(TestCase):
    """The catalog name an operator types doesn't always match the name
    bench actually registers an app under (bench derives it from the
    cloned repo's own hooks.py). get_app must discover that real name via
    apps.txt and record it, and every other command that names an
    already-fetched app must use the resolved name, not the catalog one."""

    def setUp(self):
        server = Server.objects.create(name='localhost')
        self.bench = Bench.objects.create(
            server=server, name='bench-a', frappe_branch='version-15'
        )
        self.app = App.objects.create(
            name='kartoza_portfolio', repo_url='https://example.com/kartoza_portfolio'
        )
        self.job = Job.objects.create(job_type=Job.TYPE_APP_FETCH, bench=self.bench)

    @patch('sites.bench_exec.exec_stream', return_value=0)
    @patch('sites.bench_exec.run_command')
    def test_get_app_records_actual_name_when_apps_txt_reveals_a_mismatch(
        self, mock_run_command, mock_exec_stream
    ):
        from . import bench_exec

        mock_run_command.side_effect = [
            (0, 'frappe\nerpnext\n'),  # apps.txt before the fetch
            (0, 'frappe\nerpnext\nportfolio\n'),  # apps.txt after the fetch
        ]

        bench_exec.get_app(self.bench, self.app, 'version-15', self.job)

        self.app.refresh_from_db()
        self.assertEqual(self.app.actual_name, 'portfolio')
        self.assertEqual(self.app.resolved_name, 'portfolio')

    @patch('sites.bench_exec.exec_stream', return_value=0)
    @patch('sites.bench_exec.run_command')
    def test_get_app_leaves_actual_name_blank_when_catalog_name_already_matches(
        self, mock_run_command, mock_exec_stream
    ):
        from . import bench_exec

        mock_run_command.side_effect = [
            (0, 'frappe\n'),
            (0, 'frappe\nkartoza_portfolio\n'),
        ]

        bench_exec.get_app(self.bench, self.app, 'version-15', self.job)

        self.app.refresh_from_db()
        self.assertEqual(self.app.actual_name, '')
        self.assertEqual(self.app.resolved_name, 'kartoza_portfolio')

    def test_install_uninstall_remove_use_resolved_name_not_catalog_name(self):
        from . import bench_exec

        self.app.actual_name = 'portfolio'
        self.app.save(update_fields=['actual_name'])
        site = Site.objects.create(
            bench=self.bench, hostname='demo.localhost', admin_password='x'
        )

        with patch('sites.bench_exec.exec_stream', return_value=0) as mock_exec_stream:
            bench_exec.install_app(self.bench, site, self.app, self.job)
            command = mock_exec_stream.call_args[0][1]
            self.assertIn('portfolio', command)
            self.assertNotIn('kartoza_portfolio', command)

        with patch('sites.bench_exec.exec_stream', return_value=0) as mock_exec_stream:
            bench_exec.uninstall_app(self.bench, site, self.app, self.job)
            command = mock_exec_stream.call_args[0][1]
            self.assertIn('portfolio', command)
            self.assertNotIn('kartoza_portfolio', command)

        with patch('sites.bench_exec.exec_stream', return_value=0) as mock_exec_stream:
            bench_exec.remove_app(self.bench, self.app, self.job)
            command = mock_exec_stream.call_args[0][1]
            self.assertIn('portfolio', command)
            self.assertNotIn('kartoza_portfolio', command)


class AppBranchesEndpointTests(APITestCase):
    """The add-app form fetches real branches instead of the operator typing
    them, so this endpoint's parsing/validation needs to be solid."""

    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username='admin@example.com', password='s3cret-pass', is_staff=True
        )
        self.client.force_authenticate(user=self.user)

    def test_branches_requires_repo_url(self):
        response = self.client.get('/api/apps/branches/')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_branches_rejects_non_http_scheme(self):
        response = self.client.get('/api/apps/branches/?repo_url=file:///etc/passwd')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    @patch('sites.views.subprocess.run')
    def test_branches_parses_git_ls_remote_output(self, mock_run):
        mock_run.return_value = MagicMock(
            stdout=(
                'abc123\trefs/heads/version-14\n'
                'def456\trefs/heads/version-15\n'
                'ghi789\trefs/tags/v1.0.0\n'
            ),
            stderr='',
        )
        response = self.client.get('/api/apps/branches/?repo_url=https://example.com/erpnext')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['branches'], ['version-14', 'version-15'])

    @patch('sites.views.subprocess.run')
    def test_branches_reports_unreachable_repo(self, mock_run):
        import subprocess

        mock_run.side_effect = subprocess.CalledProcessError(
            128, 'git', stderr='repository not found'
        )
        response = self.client.get('/api/apps/branches/?repo_url=https://example.com/missing')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


class SiteRestoreUploadTests(APITestCase):
    """Uploading a SQL dump / site_config.json / zip of files to restore a
    site directly, independent of the platform's own tracked backups."""

    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username='admin@example.com', password='s3cret-pass', is_staff=True
        )
        self.client.force_authenticate(user=self.user)
        server = Server.objects.create(name='localhost')
        bench = Bench.objects.create(server=server, name='bench-a', frappe_branch='version-15')
        self.site = Site.objects.create(
            bench=bench, hostname='demo.localhost', admin_password='pw', status=Site.STATUS_ACTIVE
        )
        # Every test here writes real chunk/staging files (upload-chunk,
        # upload-complete) — isolated so they never touch the real shared
        # UPLOAD_STAGING_DIR volume.
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        override = override_settings(UPLOAD_STAGING_DIR=tmpdir.name)
        override.enable()
        self.addCleanup(override.disable)

    def _zip_bytes(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w') as zip_file:
            zip_file.writestr('hello.txt', 'hi')
        return buffer.getvalue()

    def _tar_bytes(self):
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode='w') as tar:
            data = b'hi'
            info = tarfile.TarInfo(name='hello.txt')
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        return buffer.getvalue()

    def _upload(self, restore_type, filename, data):
        """Drive the chunked upload-chunk/upload-complete flow as a single
        chunk — the flow this platform's own chunked uploads use, just
        with test data small enough to not actually need splitting."""
        upload_id = str(uuid.uuid4())
        chunk_response = self.client.post(
            f'/api/sites/{self.site.id}/upload-chunk/',
            {
                'upload_id': upload_id,
                'chunk_index': '0',
                'chunk': SimpleUploadedFile('chunk', data),
            },
        )
        self.assertEqual(chunk_response.status_code, status.HTTP_204_NO_CONTENT)
        return self.client.post(
            f'/api/sites/{self.site.id}/upload-complete/',
            {
                'upload_id': upload_id,
                'total_chunks': '1',
                'filename': filename,
                'restore_type': restore_type,
            },
        )

    @patch('sites.views.restore_database_from_upload_task')
    def test_restore_database_reassembles_and_enqueues_job(self, mock_task):
        """Staging into the bench container must not happen inline (see
        tasks.restore_database_from_upload_task) — a slow docker call done
        synchronously here previously tripped gunicorn's worker timeout on
        large dumps. upload-complete's only job is reassembly + a fast
        local write."""
        response = self._upload('restore-database', 'dump.sql', b'-- sql dump')

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        mock_task.delay.assert_called_once()
        call_args = mock_task.delay.call_args[0]
        self.assertEqual(call_args[0], self.site.id)
        self.assertEqual(call_args[1], response.data['job_id'])
        staging_path = call_args[2]
        self.assertEqual(call_args[3], 'dump.sql')
        with open(staging_path, 'rb') as f:
            self.assertEqual(f.read(), b'-- sql dump')

    def test_upload_complete_rejects_unknown_restore_type(self):
        response = self._upload('restore-nonsense', 'dump.sql', b'-- sql dump')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_upload_complete_rejects_missing_chunks(self):
        response = self.client.post(
            f'/api/sites/{self.site.id}/upload-complete/',
            {
                'upload_id': str(uuid.uuid4()),
                'total_chunks': '3',
                'filename': 'dump.sql',
                'restore_type': 'restore-database',
            },
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_upload_status_reports_received_chunks(self):
        upload_id = str(uuid.uuid4())
        self.client.post(
            f'/api/sites/{self.site.id}/upload-chunk/',
            {'upload_id': upload_id, 'chunk_index': '2', 'chunk': SimpleUploadedFile('chunk', b'abc')},
        )
        response = self.client.get(f'/api/sites/{self.site.id}/upload-status/?upload_id={upload_id}')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['received'], [2])

    def test_upload_status_unknown_upload_id_reports_empty(self):
        response = self.client.get(
            f'/api/sites/{self.site.id}/upload-status/?upload_id={uuid.uuid4()}'
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['received'], [])

    def test_upload_chunk_rejects_non_uuid_upload_id(self):
        response = self.client.post(
            f'/api/sites/{self.site.id}/upload-chunk/',
            {'upload_id': '../../etc', 'chunk_index': '0', 'chunk': SimpleUploadedFile('chunk', b'x')},
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    @patch('sites.bench_exec.restore_site_config')
    def test_restore_site_config_accepts_valid_json(self, mock_restore):
        response = self._upload('restore-site-config', 'site_config.json', b'{"db_name": "x"}')

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        mock_restore.assert_called_once()
        job = Job.objects.get(pk=response.data['job_id'])
        self.assertEqual(job.status, Job.STATUS_SUCCESS)

    def test_restore_site_config_rejects_invalid_json(self):
        response = self._upload('restore-site-config', 'site_config.json', b'not json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    @patch('sites.bench_exec.restore_public_files')
    def test_restore_public_files_accepts_valid_zip(self, mock_restore):
        response = self._upload('restore-public-files', 'files.zip', self._zip_bytes())

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        mock_restore.assert_called_once_with(self.site.bench, self.site, self._zip_bytes(), 'zip')
        job = Job.objects.get(pk=response.data['job_id'])
        self.assertEqual(job.job_type, Job.TYPE_RESTORE_PUBLIC_FILES)

    @patch('sites.bench_exec.restore_private_files')
    def test_restore_private_files_accepts_valid_zip(self, mock_restore):
        response = self._upload('restore-private-files', 'files.zip', self._zip_bytes())

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        mock_restore.assert_called_once_with(self.site.bench, self.site, self._zip_bytes(), 'zip')
        job = Job.objects.get(pk=response.data['job_id'])
        self.assertEqual(job.job_type, Job.TYPE_RESTORE_PRIVATE_FILES)

    @patch('sites.bench_exec.restore_public_files')
    def test_restore_public_files_accepts_valid_tar(self, mock_restore):
        response = self._upload('restore-public-files', 'files.tar', self._tar_bytes())

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        mock_restore.assert_called_once_with(self.site.bench, self.site, self._tar_bytes(), 'tar')

    @patch('sites.bench_exec.restore_private_files')
    def test_restore_private_files_accepts_valid_tar(self, mock_restore):
        response = self._upload('restore-private-files', 'files.tar', self._tar_bytes())

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        mock_restore.assert_called_once_with(self.site.bench, self.site, self._tar_bytes(), 'tar')

    def test_restore_public_files_rejects_neither_zip_nor_tar(self):
        response = self._upload('restore-public-files', 'files.zip', b'not a zip or tar')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


class SiteConfigEndpointTests(APITestCase):
    """Viewing/editing site_config.json directly, as distinct from the
    upload-a-whole-file restore flow above."""

    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username='admin@example.com', password='s3cret-pass', is_staff=True
        )
        self.client.force_authenticate(user=self.user)
        server = Server.objects.create(name='localhost')
        bench = Bench.objects.create(server=server, name='bench-a', frappe_branch='version-15')
        self.site = Site.objects.create(
            bench=bench, hostname='demo.localhost', admin_password='pw', status=Site.STATUS_ACTIVE,
        )

    @patch('sites.views.bench_exec.read_site_config', return_value={'db_name': '_abc123', 'maintenance_mode': 0})
    def test_get_returns_the_current_config(self, mock_read):
        response = self.client.get(f'/api/sites/{self.site.id}/site-config/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data, {'db_name': '_abc123', 'maintenance_mode': 0})

    @patch('sites.views.bench_exec.read_site_config', side_effect=RuntimeError('not found'))
    def test_get_surfaces_a_read_failure_as_400_not_500(self, mock_read):
        response = self.client.get(f'/api/sites/{self.site.id}/site-config/')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    @patch('sites.views.bench_exec.restore_site_config')
    def test_put_rejects_a_non_object_body(self, mock_restore):
        response = self.client.put(
            f'/api/sites/{self.site.id}/site-config/', ['not', 'an', 'object'], format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        mock_restore.assert_not_called()

    @patch('sites.views.bench_exec.restore_site_config')
    def test_put_writes_the_edited_config_and_tracks_a_job(self, mock_restore):
        response = self.client.put(
            f'/api/sites/{self.site.id}/site-config/',
            {'db_name': '_abc123', 'maintenance_mode': 1},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        mock_restore.assert_called_once()
        written = json.loads(mock_restore.call_args[0][2])
        self.assertEqual(written, {'db_name': '_abc123', 'maintenance_mode': 1})

        job = Job.objects.get(pk=response.data['job_id'])
        self.assertEqual(job.job_type, Job.TYPE_RESTORE_SITE_CONFIG)
        self.assertEqual(job.status, Job.STATUS_SUCCESS)

    @patch('sites.views.bench_exec.restore_site_config', side_effect=RuntimeError('write failed'))
    def test_put_marks_the_job_failed_on_write_error(self, mock_restore):
        response = self.client.put(
            f'/api/sites/{self.site.id}/site-config/', {'db_name': '_abc123'}, format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        job = Job.objects.filter(site=self.site, job_type=Job.TYPE_RESTORE_SITE_CONFIG).latest('created_at')
        self.assertEqual(job.status, Job.STATUS_FAILED)


class RestoreDatabaseTaskTests(TestCase):
    """The slow part of a database restore (staging into the bench
    container, then `bench restore`) happens in the Celery task, reading
    from the local scratch file the view wrote — this must clean that
    scratch file up afterwards regardless of success or failure."""

    def setUp(self):
        server = Server.objects.create(name='localhost')
        self.bench = Bench.objects.create(server=server, name='bench-a')
        self.site = Site.objects.create(
            bench=self.bench, hostname='demo.localhost', admin_password='pw',
            status=Site.STATUS_ACTIVE,
        )
        self.job = Job.objects.create(job_type=Job.TYPE_RESTORE_DATABASE, site=self.site, bench=self.bench)

    @patch('sites.tasks.bench_exec.restore_database')
    @patch('sites.tasks.bench_exec.stage_uploaded_file', return_value='/bench/uploads/restore.sql')
    def test_stages_from_scratch_file_and_removes_it_on_success(self, mock_stage, mock_restore):
        from .tasks import restore_database_from_upload_task

        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(b'-- sql dump')
            staging_path = f.name

        restore_database_from_upload_task(self.site.id, self.job.id, staging_path, 'dump.sql')

        mock_stage.assert_called_once()
        stage_args = mock_stage.call_args[0]
        self.assertEqual(stage_args[0], self.bench)
        self.assertEqual(stage_args[1], staging_path)
        self.assertTrue(stage_args[2].endswith('dump.sql'))
        mock_restore.assert_called_once_with(self.bench, self.site, self.job, '/bench/uploads/restore.sql')
        self.job.refresh_from_db()
        self.assertEqual(self.job.status, Job.STATUS_SUCCESS)
        self.assertFalse(os.path.exists(staging_path))

    @patch('sites.tasks.bench_exec.restore_database', side_effect=RuntimeError('restore failed'))
    @patch('sites.tasks.bench_exec.stage_uploaded_file', return_value='/bench/uploads/restore.sql')
    def test_removes_scratch_file_even_when_restore_fails(self, mock_stage, mock_restore):
        from .tasks import restore_database_from_upload_task

        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(b'-- sql dump')
            staging_path = f.name

        restore_database_from_upload_task(self.site.id, self.job.id, staging_path, 'dump.sql')

        self.job.refresh_from_db()
        self.assertEqual(self.job.status, Job.STATUS_FAILED)
        self.assertFalse(os.path.exists(staging_path))


class UpdateAppTaskTests(TestCase):
    """Updating an app is bench-wide, but only sites that actually have it
    installed should be migrated afterwards."""

    def setUp(self):
        server = Server.objects.create(name='localhost')
        self.bench = Bench.objects.create(
            server=server, name='bench-a', frappe_branch='version-15'
        )
        self.app = App.objects.create(name='erpnext', repo_url='https://example.com/erpnext')

    @patch('sites.tasks.bench_exec.migrate_site')
    @patch('sites.tasks.bench_exec.update_app')
    def test_update_app_task_migrates_only_sites_with_app_installed(
        self, mock_update_app, mock_migrate
    ):
        from .models import SiteApp
        from .tasks import update_app_task

        site_with_app = Site.objects.create(
            bench=self.bench, hostname='with-app.localhost', admin_password='pw',
            status=Site.STATUS_ACTIVE,
        )
        SiteApp.objects.create(site=site_with_app, app=self.app)
        Site.objects.create(
            bench=self.bench, hostname='without-app.localhost', admin_password='pw',
            status=Site.STATUS_ACTIVE,
        )

        job = Job.objects.create(job_type=Job.TYPE_APP_UPDATE, bench=self.bench)
        update_app_task(self.bench.id, self.app.id, job.id, 'version-15')

        mock_update_app.assert_called_once()
        mock_migrate.assert_called_once_with(self.bench, site_with_app, job)
        job.refresh_from_db()
        self.assertEqual(job.status, Job.STATUS_SUCCESS)

    @patch('sites.tasks.bench_exec.migrate_site', side_effect=RuntimeError('migration failed'))
    @patch('sites.tasks.bench_exec.update_app')
    def test_update_app_task_fails_job_if_a_site_migration_fails(
        self, mock_update_app, mock_migrate
    ):
        from .models import SiteApp
        from .tasks import update_app_task

        site_with_app = Site.objects.create(
            bench=self.bench, hostname='with-app.localhost', admin_password='pw',
            status=Site.STATUS_ACTIVE,
        )
        SiteApp.objects.create(site=site_with_app, app=self.app)

        job = Job.objects.create(job_type=Job.TYPE_APP_UPDATE, bench=self.bench)
        update_app_task(self.bench.id, self.app.id, job.id, 'version-15')

        job.refresh_from_db()
        self.assertEqual(job.status, Job.STATUS_FAILED)
        self.assertIn('with-app.localhost', job.logs)

    @patch('sites.tasks.bench_exec.update_app', side_effect=RuntimeError('update failed'))
    def test_update_app_task_does_not_migrate_if_update_itself_fails(self, mock_update_app):
        from .models import SiteApp
        from .tasks import update_app_task

        site_with_app = Site.objects.create(
            bench=self.bench, hostname='with-app.localhost', admin_password='pw',
            status=Site.STATUS_ACTIVE,
        )
        SiteApp.objects.create(site=site_with_app, app=self.app)

        job = Job.objects.create(job_type=Job.TYPE_APP_UPDATE, bench=self.bench)
        with patch('sites.tasks.bench_exec.migrate_site') as mock_migrate:
            update_app_task(self.bench.id, self.app.id, job.id, 'version-15')
            mock_migrate.assert_not_called()

        job.refresh_from_db()
        self.assertEqual(job.status, Job.STATUS_FAILED)


class RestoreFilesPathStrippingTests(TestCase):
    """A real `bench backup --with-files` tar nests its entries under
    `<site>/public/files/...` — restoring that straight into the site's
    own public/files dir would recreate that nesting inside itself, so
    the known prefix must be stripped."""

    def setUp(self):
        server = Server.objects.create(name='localhost')
        self.bench = Bench.objects.create(server=server, name='bench-a')
        self.site = Site.objects.create(
            bench=self.bench, hostname='demo.localhost', admin_password='pw'
        )

    def test_strip_before_marker_handles_bench_backup_style_path(self):
        from . import bench_exec

        transform = bench_exec._strip_before_marker('public/files/')
        self.assertEqual(
            transform('./demo.localhost/public/files/image.png'), 'image.png'
        )

    def test_strip_before_marker_leaves_flat_paths_unchanged(self):
        from . import bench_exec

        transform = bench_exec._strip_before_marker('public/files/')
        self.assertEqual(transform('image.png'), 'image.png')

    @patch('sites.bench_exec.run_command', return_value=(0, ''))
    @patch('sites.bench_exec.container_exec.put_tar_contents')
    def test_restore_public_files_passes_a_working_strip_transform(self, mock_put_tar, mock_run_command):
        from . import bench_exec

        bench_exec.restore_public_files(self.bench, self.site, b'tar-bytes', 'tar')

        transform_name = mock_put_tar.call_args.kwargs['transform_name']
        self.assertEqual(
            transform_name('./demo.localhost/public/files/image.png'), 'image.png'
        )

    @patch('sites.bench_exec.run_command', return_value=(0, ''))
    @patch('sites.bench_exec.container_exec.put_tar_contents')
    def test_restore_private_files_strips_private_marker_not_public(self, mock_put_tar, mock_run_command):
        from . import bench_exec

        bench_exec.restore_private_files(self.bench, self.site, b'tar-bytes', 'tar')

        transform_name = mock_put_tar.call_args.kwargs['transform_name']
        self.assertEqual(
            transform_name('./demo.localhost/private/files/secret.txt'), 'secret.txt'
        )
        # A public/files path isn't the private marker, so it's untouched.
        self.assertEqual(
            transform_name('./demo.localhost/public/files/image.png'),
            './demo.localhost/public/files/image.png',
        )


class SiteUninstallAppTests(APITestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username='admin@example.com', password='s3cret-pass', is_staff=True
        )
        self.client.force_authenticate(user=self.user)
        server = Server.objects.create(name='localhost')
        self.bench = Bench.objects.create(server=server, name='bench-a')
        self.app = App.objects.create(name='erpnext', repo_url='https://example.com/erpnext')
        self.site = Site.objects.create(
            bench=self.bench, hostname='demo.localhost', admin_password='pw',
            status=Site.STATUS_ACTIVE,
        )

    @patch('sites.views.uninstall_app_task')
    def test_uninstall_app_rejects_app_not_installed(self, mock_task):
        response = self.client.post(
            f'/api/sites/{self.site.id}/uninstall-app/', {'app_id': self.app.id}
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        mock_task.delay.assert_not_called()

    @patch('sites.views.uninstall_app_task')
    def test_uninstall_app_enqueues_job_for_installed_app(self, mock_task):
        from .models import SiteApp

        SiteApp.objects.create(site=self.site, app=self.app)

        response = self.client.post(
            f'/api/sites/{self.site.id}/uninstall-app/', {'app_id': self.app.id}
        )
        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        mock_task.delay.assert_called_once()


class UninstallAppTaskTests(TestCase):
    def setUp(self):
        server = Server.objects.create(name='localhost')
        self.bench = Bench.objects.create(server=server, name='bench-a')
        self.app = App.objects.create(name='erpnext', repo_url='https://example.com/erpnext')
        self.site = Site.objects.create(
            bench=self.bench, hostname='demo.localhost', admin_password='pw',
            status=Site.STATUS_ACTIVE,
        )

    @patch('sites.tasks.bench_exec.uninstall_app')
    def test_uninstall_app_task_removes_site_app_record(self, mock_uninstall):
        from .models import SiteApp
        from .tasks import uninstall_app_task

        SiteApp.objects.create(site=self.site, app=self.app)
        job = Job.objects.create(job_type=Job.TYPE_APP_UNINSTALL, site=self.site, bench=self.bench)

        uninstall_app_task(self.site.id, self.app.id, job.id)

        mock_uninstall.assert_called_once()
        self.assertFalse(SiteApp.objects.filter(site=self.site, app=self.app).exists())
        job.refresh_from_db()
        self.assertEqual(job.status, Job.STATUS_SUCCESS)


def _mock_connection(cursor):
    """A MagicMock standing in for a pymysql connection, supporting the
    `with conn.cursor() as cursor:` pattern run_sql/list_databases use."""
    conn = MagicMock()
    conn.cursor.return_value.__enter__.return_value = cursor
    conn.cursor.return_value.__exit__.return_value = False
    return conn


class SqlPlaygroundModuleTests(TestCase):
    """pymysql itself is never actually invoked here — mocked at the
    connection boundary — these check the orchestration: system schemas
    are hidden, an unknown database is rejected before connecting, a
    failed statement rolls back, and multi-statement/large-result-set
    handling behaves."""

    @patch('sites.sql_playground.pymysql.connect')
    def test_list_databases_excludes_system_schemas(self, mock_connect):
        cursor = MagicMock()
        cursor.fetchall.return_value = [
            ('information_schema',), ('mysql',), ('performance_schema',), ('sys',),
            ('_def456',), ('_abc123',),
        ]
        mock_connect.return_value = _mock_connection(cursor)

        result = sql_playground.list_databases()

        self.assertEqual(result, ['_abc123', '_def456'])  # sorted too

    @patch('sites.sql_playground.pymysql.connect')
    @patch('sites.sql_playground.list_databases', return_value=['_abc123'])
    def test_run_sql_rejects_unknown_database(self, mock_list, mock_connect):
        with self.assertRaises(ValueError):
            sql_playground.run_sql('not-a-real-db', 'SELECT 1')
        mock_connect.assert_not_called()

    @patch('sites.sql_playground.pymysql.connect')
    @patch('sites.sql_playground.list_databases', return_value=['_abc123'])
    def test_run_sql_returns_affected_rows_for_a_write_statement(self, mock_list, mock_connect):
        cursor = MagicMock(description=None, rowcount=3)
        cursor.nextset.return_value = False
        mock_connect.return_value = _mock_connection(cursor)

        results = sql_playground.run_sql('_abc123', "UPDATE tabUser SET enabled=1")

        self.assertEqual(results, [{'affected_rows': 3}])
        mock_connect.return_value.commit.assert_called_once()

    @patch('sites.sql_playground.pymysql.connect')
    @patch('sites.sql_playground.list_databases', return_value=['_abc123'])
    def test_run_sql_returns_columns_and_rows_for_a_select(self, mock_list, mock_connect):
        cursor = MagicMock(description=[('id',), ('name',)])
        cursor.fetchmany.return_value = [(1, 'Ada'), (2, 'Grace')]
        cursor.fetchone.return_value = None
        cursor.nextset.return_value = False
        mock_connect.return_value = _mock_connection(cursor)

        results = sql_playground.run_sql('_abc123', 'SELECT id, name FROM tabUser')

        self.assertEqual(results, [{
            'columns': ['id', 'name'], 'rows': [[1, 'Ada'], [2, 'Grace']], 'truncated': False,
        }])

    @patch('sites.sql_playground.pymysql.connect')
    @patch('sites.sql_playground.list_databases', return_value=['_abc123'])
    def test_run_sql_flags_truncated_when_more_rows_remain(self, mock_list, mock_connect):
        cursor = MagicMock(description=[('id',)])
        cursor.fetchmany.return_value = [(1,)]
        cursor.fetchone.return_value = (2,)  # a row beyond the returned page
        cursor.nextset.return_value = False
        mock_connect.return_value = _mock_connection(cursor)

        results = sql_playground.run_sql('_abc123', 'SELECT id FROM tabUser')

        self.assertTrue(results[0]['truncated'])

    @patch('sites.sql_playground.pymysql.connect')
    @patch('sites.sql_playground.list_databases', return_value=['_abc123'])
    def test_run_sql_handles_multiple_statements(self, mock_list, mock_connect):
        cursor = MagicMock(description=None, rowcount=1)

        def nextset_side_effect():
            if not nextset_side_effect.called:
                nextset_side_effect.called = True
                cursor.description = [('id',), ('name',)]
                cursor.fetchmany.return_value = [(1, 'a')]
                cursor.fetchone.return_value = None
                return True
            return False
        nextset_side_effect.called = False
        cursor.nextset.side_effect = nextset_side_effect
        mock_connect.return_value = _mock_connection(cursor)

        results = sql_playground.run_sql('_abc123', "INSERT INTO t VALUES (1); SELECT id, name FROM t;")

        self.assertEqual(results[0], {'affected_rows': 1})
        self.assertEqual(results[1]['columns'], ['id', 'name'])
        self.assertEqual(results[1]['rows'], [[1, 'a']])

    @patch('sites.sql_playground.pymysql.connect')
    @patch('sites.sql_playground.list_databases', return_value=['_abc123'])
    def test_run_sql_rolls_back_and_reraises_on_failure(self, mock_list, mock_connect):
        cursor = MagicMock()
        cursor.execute.side_effect = pymysql.err.ProgrammingError('syntax error')
        mock_connect.return_value = _mock_connection(cursor)

        with self.assertRaises(pymysql.err.ProgrammingError):
            sql_playground.run_sql('_abc123', 'NOT VALID SQL')

        mock_connect.return_value.rollback.assert_called_once()
        mock_connect.return_value.commit.assert_not_called()


class SqlPlaygroundViewTests(APITestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_user(
            username='admin@example.com', password='s3cret-pass', is_staff=True,
        )
        self.member = get_user_model().objects.create_user(
            username='member@example.com', password='s3cret-pass',
        )

    def test_non_admin_cannot_list_databases(self):
        self.client.force_authenticate(user=self.member)
        response = self.client.get('/api/sql/databases/')
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_non_admin_cannot_run_queries(self):
        self.client.force_authenticate(user=self.member)
        response = self.client.post('/api/sql/query/', {'database': '_abc123', 'sql': 'SELECT 1'})
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    @patch('sites.views.sql_playground.list_databases', return_value=['_abc123', '_def456'])
    def test_admin_lists_databases_without_a_matching_site(self, mock_list):
        self.client.force_authenticate(user=self.admin)
        response = self.client.get('/api/sql/databases/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            response.data['databases'],
            [{'name': '_abc123', 'site': None}, {'name': '_def456', 'site': None}],
        )

    @patch('sites.views.bench_exec.get_db_name', return_value='_abc123')
    @patch('sites.views.sql_playground.list_databases', return_value=['_abc123', '_def456'])
    def test_admin_lists_databases_labelled_with_their_site_hostname(self, mock_list, mock_get_db_name):
        server = Server.objects.create(name='localhost')
        bench = Bench.objects.create(server=server, name='bench-a')
        Site.objects.create(
            bench=bench, hostname='demo.localhost', admin_password='pw', status=Site.STATUS_ACTIVE,
        )

        self.client.force_authenticate(user=self.admin)
        response = self.client.get('/api/sql/databases/')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            response.data['databases'],
            [{'name': '_abc123', 'site': 'demo.localhost'}, {'name': '_def456', 'site': None}],
        )

    def test_query_requires_database_and_sql(self):
        self.client.force_authenticate(user=self.admin)
        response = self.client.post('/api/sql/query/', {'database': '_abc123'})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    @patch('sites.views.sql_playground.run_sql', return_value=[{'affected_rows': 1}])
    def test_admin_runs_a_query(self, mock_run):
        self.client.force_authenticate(user=self.admin)
        response = self.client.post('/api/sql/query/', {'database': '_abc123', 'sql': 'DELETE FROM t WHERE id=1'})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['results'], [{'affected_rows': 1}])
        mock_run.assert_called_once_with('_abc123', 'DELETE FROM t WHERE id=1')

    @patch('sites.views.sql_playground.run_sql', side_effect=pymysql.err.ProgrammingError('bad sql'))
    def test_sql_error_is_reported_as_400_not_500(self, mock_run):
        self.client.force_authenticate(user=self.admin)
        response = self.client.post('/api/sql/query/', {'database': '_abc123', 'sql': 'NOT VALID'})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('bad sql', response.data['detail'])

    @patch('sites.views.sql_playground.run_sql', side_effect=ValueError('"x" is not a known database.'))
    def test_unknown_database_is_reported_as_400(self, mock_run):
        self.client.force_authenticate(user=self.admin)
        response = self.client.post('/api/sql/query/', {'database': 'x', 'sql': 'SELECT 1'})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
