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
from charmhelpers.core.templating import render
from charms.reactive import when, when_not, set_flag, clear_flag
from charmhelpers.core.host import service_start, service_restart, service_stop
from charmhelpers.core.hookenv import status_set, open_port, close_port, config, local_unit

########################################################################
# Installation
########################################################################
@when('java.installed')
@when_not('thingsboard.downloaded')
def download_service():
    download_thingsboard()
    #kv.set('initial_state', True)
    status_set('blocked', 'Waiting for relation with PostgreSQL')
    set_flag('thingsboard.downloaded')

@when('thingsboard.downloaded', 'postgres.connected')
@when_not('postgresdatabase.created')
def configure_database(postgres):
    database = local_unit().replace('/', '_')
    postgres.set_database(database)
    status_set('maintenance', 'Postgres database "{}" has been created'.format(database))
    set_flag('postgresdatabase.created')

@when('postgresdatabase.created', 'postgres.master.available')
@when_not('thingsboardpostgres.connected')
def connect_thingsboard(postgres):
    port = config()['port']
    conn_str = postgres.master
    render(source='thingsboard.yml',
           target='/etc/thingsboard/conf/thingsboard.yml',
           context={
               'port': port,
               'host': conn_str.host,
               'psqlport': conn_str.port,
               'database': conn_str.dbname,
               'username': conn_str.user,
               'password': conn_str.password
           })
    status_set('maintenance', 'Relation with PostgreSQL has been established')
    set_flag('thingsboardpostgres.connected')

@when('thingsboardpostgres.connected', 'postgres.master.available')
@when_not('thingsboard.started')
def start_thingsboard(postgres):
    import psycopg2
    port = config()['port']
    conn_str = postgres.master
    try:
        conn = psycopg2.connect(database=conn_str.dbname, user = conn_str.user, password = conn_str.password, host = conn_str.host, port = conn_str.port)
        cur = conn.cursor()
        cur.execute("select relname from pg_class where relkind='r' and relname !~ '^(pg_|sql_)';")
        tables = cur.fetchall()
        if len(tables) == 0:
            subprocess.check_call(['sudo','/usr/share/thingsboard/bin/install/install.sh'])
            service_start('thingsboard')
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

@when('thingsboard.started', 'config.changed', 'postgres.master.available')
def change_configuration(postgres):
    status_set('maintenance', 'Configuring ThingsBoard')
    conf = config()
    conn_str = postgres.master
    change_config(conf, conn_str)
    status_set('active', 'ThingsBoard is running and uses PostgreSQL')

@when('thingsboard.started')
@when_not('postgres.connected')
def stop_service():
    service_stop('thingsboard')
    port = config()['port']
    close_port(port)
    status_set('blocked', 'Waiting for relation with PostgreSQL')
    set_flag('thingsboard.downloaded')
    states = ['postgresdatabase.created', 'thingsboardpostgres.connected', 'thingsboard.started']
    for state in states:
        clear_flag(state)

########################################################################
# Auxiliary methods
########################################################################
def download_thingsboard():
    status_set('maintenance', 'Installing ThingsBoard')
    subprocess.check_call(['sudo', 'pip3', 'install', 'psycopg2-binary'])
    thingsboard_path = '/opt/thingsboard'
    if not os.path.isdir(thingsboard_path):
        os.mkdir(thingsboard_path)
    fetch_handler = charmhelpers.fetch.archiveurl.ArchiveUrlFetchHandler()
    fetch_handler.download('https://github.com/thingsboard/thingsboard/releases/download/v1.3.1/thingsboard-1.3.1.deb',
                            thingsboard_path + '/thingsboard-1.3.1.deb')
    subprocess.check_call(['dpkg', '-i', '{}/thingsboard-1.3.1.deb'.format(thingsboard_path)])

def change_config(conf, conn_str):
    port = conf['port']
    old_port = conf.previous('port')
    if old_port is not None and old_port != port:
        render(source='thingsboard.yml',
               target='/etc/thingsboard/conf/thingsboard.yml',
               context={
                   'port': port,
                   'host': conn_str.host,
                   'psqlport': conn_str.port,
                   'database': conn_str.dbname,
                   'username': conn_str.user,
                   'password': conn_str.password
               })
        close_port(old_port)
        open_port(port)
        service_restart('thingsboard')
