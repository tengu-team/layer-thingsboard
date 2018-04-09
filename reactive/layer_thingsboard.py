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
from charmhelpers.core.hookenv import status_set, open_port, close_port, config, service_name, unit_private_ip

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
@when_not('thingsboard.pgdatabase.created', 'cassandra.available')
def create_database(postgres):
    database = service_name()
    postgres.set_database(database)
    status_set('maintenance', 'Postgres database "{}" has been created'.format(database))
    set_flag('thingsboard.pgdatabase.created')

@when('thingsboard.pgdatabase.created', 'postgres.master.available')
@when_not('thingsboard.started')
def start_thingsboardpg(postgres):
    status_set('maintenance', 'Configuring ThingsBoard')
    conn_str = postgres.master
    context = {'port': str(config()['port']),
               'zk_enabled': 'false',
               'zk_urls': 'localhost:2181',
               'rpc_host': unit_private_ip(),
               'type_database': 'sql',
               'postgres_host': conn_str.host,
               'postgres_port': conn_str.port,
               'postgres_database': conn_str.dbname,
               'postgres_username': conn_str.user,
               'postgres_password': conn_str.password}
    render_conf_file(context)
    kv.set('database_parameters', context)
    run_install_script()
    open_port(config()['port'])
    status_set('active', 'ThingsBoard is running and uses PostgreSQL')
    set_flag('thingsboard.started')

############################################################################
#                       Integration with Cassandra                         #
############################################################################
@when('thingsboard.installed', 'cassandra.available')
@when_not('thingsboard.cassandra.connected', 'postgres.connected')
def configure_cassandra(cassandra):
    status_set('maintenance', 'Configuring ThingsBoard')
    port = config()['port']
    list_nodes = ''
    for conv in cassandra.conversations():
        list_nodes += '{}:{}, '.format(conv.get_remote('private-address'), conv.get_remote('native_transport_port'))
    list_nodes = list_nodes[:-2]
    context={'port': str(port),
             'zk_enabled': 'false',
             'zk_urls': 'localhost:2181',
             'rpc_host': unit_private_ip(),
             'type_database': 'cassandra',
             'cassandra_cluster_name': cassandra.cluster_name(),
             'cassandra_list_nodes': list_nodes,
             'use_credentials': 'true',
             'cassandra_username': cassandra.username(),
             'cassandra_password': cassandra.password()}
    render_conf_file(context)
    kv.set('database_parameters', context)
    set_flag('thingsboard.cassandra.connected')

@when('thingsboard.cassandra.connected')
@when_not('thingsboard.started')
def start_thingsboardcassdb():
    run_install_script()
    open_port(config()['port'])
    status_set('active', 'ThingsBoard is running and uses Cassandra')
    set_flag('thingsboard.started')

############################################################################
#                              Common methods                              #
############################################################################
@when('thingsboard.started', 'zookeeper.ready')
@when_not('thingsboard.zookeeper.connected')
def configure_zookeeper(zookeeper):
    status_set('maintenance', 'Configuring ThingsBoard')
    zk_urls = ''
    for zk in zookeeper.zookeepers():
        zk_urls += '{}:{}, '.format(zk['host'], zk['port'])
    zk_urls = zk_urls[:-2]
    context = kv.get('database_parameters')
    context['zk_enabled'] = 'true'
    context['zk_urls'] = zk_urls
    render_conf_file(context)
    kv.set('database_parameters', context)
    open_port(9001)
    service_restart('thingsboard')
    if context['type_database'] == 'sql':
        database = 'PostgreSQL'
    else:
        database = 'Cassandra'
    status_set('active', 'ThingsBoard is running and uses {} & Zookeeper'.format(database))
    set_flag('thingsboard.zookeeper.connected')

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
        kv.set('database_parameters', context)
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
def stop_thingsboard():
    service_stop('thingsboard')
    close_port(config()['port'])
    context = kv.get('database_parameters')
    if context['type_database'] == 'sql':
        states = ['thingsboard.pgdatabase.created', 'thingsboard.started']
    else:
        states = ['thingsboard.cassandra.connected', 'thingsboard.started']
    for state in states:
        clear_flag(state)
    status_set('blocked', 'Waiting for relation with database')

@when('thingsboard.zookeeper.connected')
@when_not('zookeeper.ready')
def inactivate_zookeeper():
    context = kv.get('database_parameters')
    context['zk_enabled'] = 'false'
    context['zk_urls'] = 'localhost:2181'
    render_conf_file(context)
    kv.set('database_parameters', context)
    close_port(9001)
    clear_flag('thingsboard.zookeeper.connected')

############################################################################
#                             HTTP interface                               #
############################################################################
@when('thingsboard.started', 'http.available')
@when_not('http.configured')
def configure_http(http):
    http.configure(config()['port'])
    set_flag('http.configured')

############################################################################
#                          Auxiliary methods                               #
############################################################################
def install_thingsboard():
    status_set('maintenance', 'Installing ThingsBoard')
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
