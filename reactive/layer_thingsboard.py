#!/usr/bin/python3
# Copyright (C) 2017  Qrama
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.
# pylint: disable=c0111,c0103,c0301

import os
import subprocess
import charmhelpers.fetch.archiveurl
from charmhelpers.core import unitdata
from charmhelpers.core.templating import render
from charms.reactive import when, when_not, set_flag, clear_flag
from charmhelpers.core.host import service_start, service_restart, service_stop
from charmhelpers.core.hookenv import status_set, open_port, close_port, config, local_unit

kv = unitdata.kv()

############################################################################
#                                Installation                              #
############################################################################
@when('java.installed')
@when_not('thingsboard.installed')
def install_service():
    install_thingsboard()
    status_set('blocked', 'Waiting for relation with database')
    set_flag('thingsboard.installed')

@when('thingsboard.installed', 'config.changed')
@when_not('postgres.connected', 'cassandra.available')
def change_configuration():
    context={'port': str(config()['port'])}
    render_conf_file(context)

############################################################################
#                     Integration with PostgreSQL                          #
############################################################################
@when('thingsboard.installed', 'postgres.connected')
@when_not('postgresdatabase.created', 'cassandra.available')
def configure_database(postgres):
    database = local_unit().replace('/', '_')
    postgres.set_database(database)
    status_set('maintenance', 'Postgres database "{}" has been created'.format(database))
    set_flag('postgresdatabase.created')

@when('postgresdatabase.created', 'postgres.master.available')
@when_not('thingsboard.postgres.connected')
def connect_thingsboard(postgres):
    conn_str = postgres.master
    context = {'port': str(config()['port']),
               'type_database': 'sql',
               'host': conn_str.host,
               'psqlport': conn_str.port,
               'database': conn_str.dbname,
               'username': conn_str.user,
               'password': conn_str.password}
    render_conf_file(context)
    kv.set('database_parameters', context)
    status_set('maintenance', 'Connecting to PostgreSQL')
    set_flag('thingsboard.postgres.connected')

@when('thingsboard.postgres.connected', 'postgres.master.available')
@when_not('thingsboard.started')
def start_thingsboardpg(postgres):
    import psycopg2
    port = config()['port']
    conn_str = postgres.master
    try:
        conn = psycopg2.connect(database=conn_str.dbname, user = conn_str.user,
                                password = conn_str.password, host = conn_str.host, port = conn_str.port)
        cur = conn.cursor()
        cur.execute("select relname from pg_class where relkind='r' and relname !~ '^(pg_|sql_)';")
        tables = cur.fetchall()
        if len(tables) == 0:
            run_install_script()
        else:
            service_restart('thingsboard')
        conn.close()
        open_port(port)
        status = 'active'
        message = 'ThingsBoard is running and uses PostgreSQL'
    except:
        status = 'blocked'
        message = 'Access to PostgreSQL with Psycopg2 has failed'
    status_set(status, message)
    set_flag('thingsboard.started')

############################################################################
#                             HTTP interface                               #
############################################################################
@when('thingsboard.started', 'http.available')
@when_not('http.configured')
def configure_http(http):
    http.configure(config()['port'])
    set_flag('http.configured')

############################################################################
#                       Integration with Cassandra                         #
############################################################################
@when('thingsboard.installed', 'cassandra.available')
@when_not('thingsboard.cassandra.connected', 'postgres.connected')
def connect_to_cassandra(cassandra):
    status_set('maintenance', 'Connecting to Cassandra')
    port = config()['port']
    ip_address = cassandra.conversations()[0].get_remote('private-address')
    context={'port': str(port),
             'type_database': 'cassandra',
             'cluster_name': cassandra.cluster_name(),
             'cassandra_host': ip_address,
             'cassandra_port': cassandra.native_transport_port(),
             'use_credentials': 'true',
             'cassandra_username': cassandra.username(),
             'cassandra_password': cassandra.password()}
    render_conf_file(context)
    kv.set('database_parameters', context)
    set_flag('thingsboard.cassandra.connected')

@when('thingsboard.cassandra.connected', 'cassandra.available')
@when_not('thingsboard.started')
def start_thingsboardcassdb():
    run_install_script()
    open_port(config()['port'])
    status_set('active', 'ThingsBoard is running and uses Cassandra')
    set_flag('thingsboard.started')

############################################################################
#                              Common methods                              #
############################################################################
@when('thingsboard.started', 'config.changed')
def change_config():
    status_set('maintenance', 'Configuring ThingsBoard')
    conf = config()
    port = conf['port']
    old_port = conf.previous('port')
    if old_port is not None and port != old_port:
        context = kv.get('database_parameters')
        context['port'] = str(port)
        render_conf_file(context)
        close_port(old_port)
        open_port(port)
        service_restart('thingsboard')
    if context['type_database'] == 'sql':
        database = 'PostgreSQL'
    else:
        database = 'Cassandra'
    status_set('active', 'ThingsBoard is running and uses {}'.format(database))

@when('thingsboard.started')
@when_not('postgres.connected', 'cassandra.available')
def stop_service():
    service_stop('thingsboard')
    close_port(config()['port'])
    context = kv.get('database_parameters')
    if context['type_database'] == 'sql':
        states = ['postgresdatabase.created', 'thingsboard.postgres.connected', 'thingsboard.started']
    else:
        states = ['thingsboard.cassandra.connected', 'thingsboard.started']
    for state in states:
        clear_flag(state)
    status_set('blocked', 'Waiting for relation with database')

@when('postgres.connected', 'cassandra.available')
def set_blocked():
    service_stop('thingsboard')
    close_port(config()['port'])
    status_set('blocked', 'Please establish relation with only one database')

############################################################################
#                          Auxiliary methods                               #
############################################################################
def install_thingsboard():
    status_set('maintenance', 'Installing ThingsBoard')
    subprocess.check_call(['sudo', 'pip3', 'install', 'psycopg2-binary'])
    thingsboard_path = '/opt/thingsboard'
    if not os.path.isdir(thingsboard_path):
        os.mkdir(thingsboard_path)
    fetch_handler = charmhelpers.fetch.archiveurl.ArchiveUrlFetchHandler()
    fetch_handler.download('https://github.com/thingsboard/thingsboard/releases/download/v1.4/thingsboard-1.4.deb',
                            thingsboard_path + '/thingsboard-1.4.deb')
    subprocess.check_call(['dpkg', '-i', '{}/thingsboard-1.4.deb'.format(thingsboard_path)])
    context={'port': str(config()['port'])}
    render_conf_file(context)

def render_conf_file(context):
    render(source='thingsboard.yml',
           target='/etc/thingsboard/conf/thingsboard.yml',
           context=context)

def run_install_script():
    subprocess.check_call(['sudo','/usr/share/thingsboard/bin/install/install.sh'])
    service_start('thingsboard')
