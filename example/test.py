#!/usr/bin/env python3
import contextlib
import json
import logging
import re
import time
from datetime import datetime, timedelta, timezone

import iso8601
import pytest
import requests
from seaworthy.ps import build_process_tree
from seaworthy.testtools import MatchesPsTree
from seaworthy.utils import output_lines
from testtools.assertions import assert_that
from testtools.matchers import (
    AfterPreprocessing as After, Contains, Equals, GreaterThan, HasLength,
    LessThan, MatchesAll, MatchesAny, MatchesDict, MatchesListwise,
    MatchesRegex, MatchesSetwise, Not)

from fixtures import *  # noqa: We import these so pytest can find them.


# Turn off spam from all the random loggers that set themselves up behind us.
for logger in logging.Logger.manager.loggerDict.values():
    if isinstance(logger, logging.Logger):
        logger.setLevel(logging.WARNING)
# Turn on spam from the loggers we're interested in.
logging.getLogger('docker_helper.helper').setLevel(logging.DEBUG)


def filter_ldconfig_process(ps_rows):
    """
    Sometimes an ldconfig process running under the django user shows up.
    Filter it out.
    :param ps_rows: A list of PsRow objects.
    """
    return [row for row in ps_rows
            if not (row.ruser == 'django' and 'ldconfig' in row.args)]


@contextlib.contextmanager
def requests_client(container):
    # FIXME: please please fix me
    ports = container.inner().attrs['NetworkSettings']['Ports']
    assert len(ports) == 1
    print(ports)
    # Pick the first and only port
    port = next(iter(ports.values()))[0]['HostPort']

    with requests.Session() as session:
        def client(path, method='GET', **kwargs):
            return session.request(
                method, 'http://127.0.0.1:{}{}'.format(port, path), **kwargs)

        yield client


class TestWeb(object):
    def test_expected_processes(self, web_only_container):
        """
        When the container is running, there should be 5 running processes:
        tini, the Nginx master and worker, and the Gunicorn master and worker.
        """
        ps_rows = filter_ldconfig_process(web_only_container.list_processes())

        # Sometimes it takes a little while for the processes to settle so try
        # a few times with a delay inbetween.
        retries = 3
        delay = 0.5
        for _ in range(retries):
            if len(ps_rows) == 5:
                break
            time.sleep(delay)
            ps_rows = filter_ldconfig_process(
                web_only_container.list_processes())

        ps_tree = build_process_tree(ps_rows)

        tini_args = 'tini -- django-entrypoint.sh mysite.wsgi:application'
        gunicorn_master_args = (
            '/usr/local/bin/python /usr/local/bin/gunicorn '
            'mysite.wsgi:application --pid /var/run/gunicorn/gunicorn.pid '
            '--bind unix:/var/run/gunicorn/gunicorn.sock --umask 0117')
        gunicorn_worker_args = (
            '/usr/local/bin/python /usr/local/bin/gunicorn '
            'mysite.wsgi:application --pid /var/run/gunicorn/gunicorn.pid '
            '--bind unix:/var/run/gunicorn/gunicorn.sock --umask 0117')
        nginx_master_args = 'nginx: master process nginx -g daemon off;'
        nginx_worker_args = 'nginx: worker process'

        assert_that(
            ps_tree,
            MatchesPsTree('root', tini_args, pid=1, children=[
                MatchesPsTree('django', gunicorn_master_args, children=[
                    MatchesPsTree('django', gunicorn_worker_args),
                    # FIXME: Nginx should not be parented by Gunicorn
                    MatchesPsTree('root', nginx_master_args, children=[
                        MatchesPsTree('nginx', nginx_worker_args),
                    ]),
                ]),
            ]))

    def test_expected_processes_single_container(self, single_container):
        """
        When the container is running, there should be 7 running processes:
        tini, the Nginx master and worker, the Gunicorn master and worker, and
        the Celery worker ("solo", non-forking) and beat processes.
        """
        ps_rows = single_container.list_processes()
        ps_tree = build_process_tree(ps_rows)

        tini_args = 'tini -- django-entrypoint.sh mysite.wsgi:application'
        gunicorn_master_args = (
            '/usr/local/bin/python /usr/local/bin/gunicorn '
            'mysite.wsgi:application --pid /var/run/gunicorn/gunicorn.pid '
            '--bind unix:/var/run/gunicorn/gunicorn.sock --umask 0117')
        gunicorn_worker_args = (
            '/usr/local/bin/python /usr/local/bin/gunicorn '
            'mysite.wsgi:application --pid /var/run/gunicorn/gunicorn.pid '
            '--bind unix:/var/run/gunicorn/gunicorn.sock --umask 0117')
        nginx_master_args = 'nginx: master process nginx -g daemon off;'
        nginx_worker_args = 'nginx: worker process'
        celery_worker_args = (
            '/usr/local/bin/python /usr/local/bin/celery worker --pool=solo '
            '--pidfile worker.pid --concurrency 1')
        celery_beat_args = (
            '/usr/local/bin/python /usr/local/bin/celery beat --pidfile '
            'beat.pid')

        assert_that(
            ps_tree,
            MatchesPsTree('root', tini_args, pid=1, children=[
                MatchesPsTree('django', gunicorn_master_args, children=[
                    MatchesPsTree('django', gunicorn_worker_args),
                    # FIXME: Celery worker should not be parented by Gunicorn
                    MatchesPsTree('django', celery_worker_args),
                    # FIXME: Celery beat should not be parented by Gunicorn
                    MatchesPsTree('django', celery_beat_args),
                    # FIXME: Nginx should not be parented by Gunicorn
                    MatchesPsTree('root', nginx_master_args, children=[
                        MatchesPsTree('nginx', nginx_worker_args),
                    ]),
                ]),
            ]))

    def test_expected_processes_pod_container(self, gunicorn_container):
        """
        When the container is running, there should be 3 running processes:
        tini and the Gunicorn master and worker.
        """
        ps_rows = filter_ldconfig_process(gunicorn_container.list_processes())

        # Sometimes it takes a little while for the processes to settle so try
        # a few times with a delay inbetween.
        retries = 3
        delay = 0.5
        for _ in range(retries):
            if len(ps_rows) == 5:
                break
            time.sleep(delay)
            ps_rows = filter_ldconfig_process(
                gunicorn_container.list_processes())

        ps_tree = build_process_tree(ps_rows)

        tini_args = 'tini -- django-entrypoint.sh mysite.wsgi:application'
        gunicorn_master_args = (
            '/usr/local/bin/python /usr/local/bin/gunicorn '
            'mysite.wsgi:application --pid /var/run/gunicorn/gunicorn.pid '
            '--bind unix:/var/run/gunicorn/gunicorn.sock')
        gunicorn_worker_args = (
            '/usr/local/bin/python /usr/local/bin/gunicorn '
            'mysite.wsgi:application --pid /var/run/gunicorn/gunicorn.pid '
            '--bind unix:/var/run/gunicorn/gunicorn.sock')

        assert_that(
            ps_tree,
            MatchesPsTree('root', tini_args, pid=1, children=[
                MatchesPsTree('django', gunicorn_master_args, children=[
                    MatchesPsTree('django', gunicorn_worker_args),
                ]),
            ]))

    @pytest.mark.clean_db
    def test_database_tables_created(self, db_container, web_container):
        """
        When the web container is running, a migration should have completed
        and there should be some tables in the database.
        """
        public_tables = [
            r[1] for r in db_container.list_tables() if r[0] == 'public']
        assert_that(len(public_tables), GreaterThan(0))

    def test_admin_site_live(self, nginx_container, web_container):
        """
        When we get the /admin/ path, we should receive some HTML for the
        Django admin interface.
        """
        with requests_client(nginx_container) as client:
            response = client('/admin/')

        assert_that(response.headers['Content-Type'],
                    Equals('text/html; charset=utf-8'))
        assert_that(response.text,
                    Contains('<title>Log in | Django site admin</title>'))

    def test_static_file(self, nginx_container, web_container):
        """
        When a static file is requested, Nginx should serve the file with the
        correct mime type.
        """
        with requests_client(nginx_container) as client:
            response = client('/static/admin/css/base.css')

        assert_that(response.headers['Content-Type'], Equals('text/css'))
        assert_that(response.text, Contains('DJANGO Admin styles'))

    def test_manifest_static_storage_file(
            self, nginx_container, web_container):
        """
        When a static file that was processed by Django's
        ManifestStaticFilesStorage system is requested, that file should be
        served with a far-future 'Cache-Control' header.
        """
        hashed_svg = web_container.exec_find(
            ['static/admin/img', '-regextype', 'posix-egrep', '-regex',
             '.*\.[a-f0-9]{12}\.svg$'])
        test_file = hashed_svg[0]

        with requests_client(nginx_container) as client:
            response = client('/' + test_file)

        assert_that(response.headers['Content-Type'], Equals('image/svg+xml'))
        assert_that(response.headers['Cache-Control'],
                    Equals('max-age=315360000, public, immutable'))

    def test_django_compressor_js_file(self, nginx_container, web_container):
        """
        When a static JavaScript file that was processed by django_compressor
        is requested, that file should be served with a far-future
        'Cache-Control' header.
        """
        compressed_js = web_container.exec_find(
            ['static/CACHE/js', '-name', '*.js'])
        test_file = compressed_js[0]

        with requests_client(nginx_container) as client:
            response = client('/' + test_file)

        assert_that(response.headers['Content-Type'],
                    Equals('application/javascript'))
        assert_that(response.headers['Cache-Control'],
                    Equals('max-age=315360000, public, immutable'))

    def test_django_compressor_css_file(self, nginx_container, web_container):
        """
        When a static CSS file that was processed by django_compressor is
        requested, that file should be served with a far-future 'Cache-Control'
        header.
        """
        compressed_js = web_container.exec_find(
            ['static/CACHE/css', '-name', '*.css'])
        test_file = compressed_js[0]

        with requests_client(nginx_container) as client:
            response = client('/' + test_file)

        assert_that(response.headers['Content-Type'], Equals('text/css'))
        assert_that(response.headers['Cache-Control'],
                    Equals('max-age=315360000, public, immutable'))

    def test_gzip_css_compressed(self, nginx_container, web_container):
        """
        When a CSS file larger than 1024 bytes is requested and the
        'Accept-Encoding' header lists gzip as an accepted encoding, the file
        should be served gzipped.
        """
        css_to_gzip = web_container.exec_find(
            ['static', '-name', '*.css', '-size', '+1024c'])
        test_file = css_to_gzip[0]

        with requests_client(nginx_container) as client:
            response = client('/' + test_file,
                              headers={'Accept-Encoding': 'gzip'})

        assert_that(response.headers['Content-Type'], Equals('text/css'))
        assert_that(response.headers['Content-Encoding'], Equals('gzip'))
        assert_that(response.headers['Vary'], Equals('Accept-Encoding'))

    def test_gzip_woff_not_compressed(self, nginx_container, web_container):
        """
        When a .woff file larger than 1024 bytes is requested and the
        'Accept-Encoding' header lists gzip as an accepted encoding, the file
        should not be served gzipped as it is already a compressed format.
        """
        woff_to_not_gzip = web_container.exec_find(
            ['static', '-name', '*.woff', '-size', '+1024c'])
        test_file = woff_to_not_gzip[0]

        with requests_client(nginx_container) as client:
            response = client('/' + test_file,
                              headers={'Accept-Encoding': 'gzip'})

        assert_that(response.headers['Content-Type'],
                    Equals('application/font-woff'))
        assert_that(response.headers, MatchesAll(
            Not(Contains('Content-Encoding')),
            Not(Contains('Vary')),
        ))

    def test_gzip_accept_encoding_respected(
            self, nginx_container, web_container):
        """
        When a CSS file larger than 1024 bytes is requested and the
        'Accept-Encoding' header does not list gzip as an accepted encoding,
        the file should not be served gzipped, but the 'Vary' header should be
        set to 'Accept-Encoding'.
        """
        css_to_gzip = web_container.exec_find(
            ['static', '-name', '*.css', '-size', '+1024c'])
        test_file = css_to_gzip[0]

        with requests_client(nginx_container) as client:
            response = client('/' + test_file,
                              headers={'Accept-Encoding': ''})

        assert_that(response.headers['Content-Type'], Equals('text/css'))
        assert_that(response.headers, Not(Contains('Content-Encoding')))
        # The Vary header should be set if there is a *possibility* that this
        # file will be served with a different encoding.
        assert_that(response.headers['Vary'], Equals('Accept-Encoding'))

    def test_gzip_via_compressed(self, nginx_container, web_container):
        """
        When a CSS file larger than 1024 bytes is requested and the
        'Accept-Encoding' header lists gzip as an accepted encoding and the
        'Via' header is set, the file should be served gzipped.
        """
        css_to_gzip = web_container.exec_find(
            ['static', '-name', '*.css', '-size', '+1024c'])
        test_file = css_to_gzip[0]

        with requests_client(nginx_container) as client:
            response = client(
                '/' + test_file,
                headers={'Accept-Encoding': 'gzip', 'Via': 'Internet.org'})

        assert_that(response.headers['Content-Type'], Equals('text/css'))
        assert_that(response.headers['Content-Encoding'], Equals('gzip'))
        assert_that(response.headers['Vary'], Equals('Accept-Encoding'))

    def test_gzip_small_file_not_compressed(
            self, nginx_container, web_container):
        """
        When a CSS file smaller than 1024 bytes is requested and the
        'Accept-Encoding' header lists gzip as an accepted encoding, the file
        should not be served gzipped.
        """
        css_to_gzip = web_container.exec_find(
            ['static', '-name', '*.css', '-size', '-1024c'])
        test_file = css_to_gzip[0]

        with requests_client(nginx_container) as client:
            response = client('/' + test_file,
                              headers={'Accept-Encoding': 'gzip'})

        assert_that(response.headers['Content-Type'], Equals('text/css'))
        assert_that(response.headers, MatchesAll(
            Not(Contains('Content-Encoding')),
            Not(Contains('Vary')),
        ))


class TestNginx(object):
    def test_expected_processes(self, nginx_only_container):
        """
        When the container is running, there should be 2 running processes:
        the Nginx master and worker processes.
        """
        ps_rows = nginx_only_container.list_processes()
        ps_tree = build_process_tree(ps_rows)

        nginx_master_args = 'nginx: master process nginx -g daemon off;'
        nginx_worker_args = 'nginx: worker process'

        assert_that(
            ps_tree,
            MatchesPsTree('root', nginx_master_args, pid=1, children=[
                MatchesPsTree('nginx', nginx_worker_args),
            ]))

    def test_nginx_access_logs(self, nginx_container, web_container):
        """
        When a request has been made to the container, Nginx logs access logs
        to stdout
        """
        # Wait a little bit so that previous tests' requests have been written
        # to the log.
        time.sleep(0.2)
        before_lines = nginx_container.stdout_logs()

        # Make a request to see the logs for it
        with requests_client(nginx_container) as client:
            client('/')

        # Wait a little bit so that our request has been written to the log.
        time.sleep(0.2)
        after_lines = nginx_container.stdout_logs()

        new_lines = after_lines[len(before_lines):]
        assert_that(len(new_lines), GreaterThan(0))

        # Find the Nginx log line
        nginx_lines = [l for l in new_lines if re.match(r'^\{ "time": .+', l)]
        assert_that(nginx_lines, HasLength(1))

        now = datetime.now(timezone.utc)
        assert_that(json.loads(nginx_lines[0]), MatchesDict({
            # Assert time is valid and recent
            'time': After(iso8601.parse_date, MatchesAll(
                MatchesAny(LessThan(now), Equals(now)),
                MatchesAny(GreaterThan(now - timedelta(seconds=5)))
            )),

            'request': Equals('GET / HTTP/1.1'),
            'status': Equals(404),
            'body_bytes_sent': GreaterThan(0),
            'request_time': LessThan(1.0),
            'http_referer': Equals(''),

            # Assert remote_addr is an IPv4 (roughly)
            'remote_addr': MatchesRegex(
                r'^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$'),
            'http_host': MatchesRegex(r'^127.0.0.1:\d{4,5}$'),
            'http_user_agent': MatchesRegex(r'^python-requests/'),

            # Not very interesting empty fields
            'remote_user': Equals(''),
            'http_via': Equals(''),
            'http_x_forwarded_proto': Equals(''),
            'http_x_forwarded_for': Equals(''),
        }))


class TestCeleryWorker(object):
    def test_expected_processes(self, worker_only_container):
        """
        When the container is running, there should be 3 running processes:
        tini, and the Celery worker master and worker.
        """
        ps_rows = worker_only_container.list_processes()
        ps_tree = build_process_tree(ps_rows)

        tini_args = 'tini -- django-entrypoint.sh celery worker'
        celery_master_args = (
            '/usr/local/bin/python /usr/local/bin/celery worker '
            '--concurrency 1')
        celery_worker_args = (
            '/usr/local/bin/python /usr/local/bin/celery worker '
            '--concurrency 1')

        assert_that(
            ps_tree,
            MatchesPsTree('root', tini_args, pid=1, children=[
                MatchesPsTree('django', celery_master_args, children=[
                    MatchesPsTree('django', celery_worker_args),
                ]),
            ]))

    @pytest.mark.clean_amqp
    def test_amqp_queues_created(self, amqp_container, worker_container):
        """
        When the worker container is running, the three default Celery queues
        should have been created in RabbitMQ.
        """
        # FIXME: This should be a method on RabbitMQContainer.
        rabbitmq_output = amqp_container.exec_rabbitmqctl(
            'list_queues', ['-p', '/mysite'])
        rabbitmq_lines = output_lines(rabbitmq_output)
        rabbitmq_data = [line.split(None, 1) for line in rabbitmq_lines]

        assert_that(rabbitmq_data, MatchesSetwise(*map(MatchesListwise, (
            [Equals('celery'), Equals('0')],
            [MatchesRegex(r'^celeryev\..+'), Equals('0')],
            [MatchesRegex(r'^celery@.+\.celery\.pidbox$'), Equals('0')],
        ))))


class TestCeleryBeat(object):
    def test_expected_processes(self, beat_only_container):
        """
        When the container is running, there should be 2 running processes:
        tini, and the Celery beat process.
        """
        ps_rows = beat_only_container.list_processes()
        ps_tree = build_process_tree(ps_rows)

        tini_args = 'tini -- django-entrypoint.sh celery beat'
        celery_beat_args = '/usr/local/bin/python /usr/local/bin/celery beat'

        assert_that(
            ps_tree,
            MatchesPsTree('root', tini_args, pid=1, children=[
                MatchesPsTree('django', celery_beat_args),
            ]))
