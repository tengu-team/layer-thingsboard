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
from charms.reactive import when, when_not, set_flag
from charmhelpers.core.host import service_start, service_restart
from charmhelpers.core.hookenv import status_set, open_port, close_port, config, local_unit

########################################################################
# Installation
########################################################################
@when('java.installed', 'apt.installed.python3-pip')
@when_not('ruamel.installed')
def install_pyyaml():
    install_ruamel()
    set_flag('ruamel.installed')

@when('ruamel.installed')
@when_not('thingsboard.installed')
def install_service():
    install_thingsboard()
    status_set('active', 'ThingsBoard is running and uses HSQLDB.')
    set_flag('thingsboard.installed')

@when('thingsboard.installed', 'config.changed')
def change_configuration():
    status_set('maintenance', 'configuring ThingsBoard')
    conf = config()
    change_config(conf)
    status_set('active', 'ThingsBoard is running and uses HSQLDB.')

@when('thingsboard.installed', 'postgres.connected')
@when_not('postgresdatabase.created')
def configure_database(postgres):
    database = local_unit().replace('/', '_')
    postgres.set_database(database)
    status_set('active', 'postgres database "{}" has been created'.format(database))
    set_flag('postgresdatabase.created')

@when('postgresdatabase.created', 'postgres.master.available')
@when_not('thingsboardpostgres.connected')
def connect_thingsboard(postgres):
    from ruamel.yaml import YAML
    yaml = YAML(typ='rt')
    yaml.preserve_quotes = True
    conn_str = postgres.master
    with open('templates/thingsboard.yml', 'r') as f:
        data = yaml.load(f)
    data['spring']['jpa']['database-platform'] = "${SPRING_JPA_DATABASE_PLATFORM:org.hibernate.dialect.PostgreSQLDialect}"
    data['spring']['datasource']['driverClassName'] = "${SPRING_DRIVER_CLASS_NAME:org.postgresql.Driver}"
    data['spring']['datasource']['url'] = "${SPRING_DATASOURCE_URL:jdbc:postgresql://" + conn_str.host + ":" + conn_str.port + "/" + conn_str.dbname + "}"
    data['spring']['datasource']['username'] = "${SPRING_DATASOURCE_USERNAME:" + conn_str.user + "}"
    data['spring']['datasource']['password'] = "${SPRING_DATASOURCE_PASSWORD:" + conn_str.password + "}"
    with open('/etc/thingsboard/conf/thingsboard.yml', 'w') as wf:
        yaml.dump(data, wf)
    service_restart('thingsboard')
    status_set('active', 'ThingsBoard is running and uses PostgreSQL.')
    set_flag('thingsboardpostgres.connected')

########################################################################
# Auxiliary methods
########################################################################
def install_ruamel():
    status_set('maintenance', 'installing ruamel')
    subprocess.check_call(['sudo', 'pip3', 'install', 'pyyaml', 'ruamel.yaml'])

def install_thingsboard():
    status_set('maintenance', 'installing ThingsBoard')
    thingsboard_path = '/opt/thingsboard'
    if not os.path.isdir(thingsboard_path):
        os.mkdir(thingsboard_path)
    fetch_handler = charmhelpers.fetch.archiveurl.ArchiveUrlFetchHandler()
    fetch_handler.download('https://github.com/thingsboard/thingsboard/releases/download/v1.3.1/thingsboard-1.3.1.deb',
                            thingsboard_path + '/thingsboard-1.3.1.deb')
    subprocess.check_call(['dpkg', '-i', '{}/thingsboard-1.3.1.deb'.format(thingsboard_path)])
    subprocess.check_call(['/usr/share/thingsboard/bin/install/install.sh'])
    service_start('thingsboard')
    port = config()['port']
    open_port(port)

def change_config(conf):
    port = conf['port']
    old_port = conf.previous('port')
    if old_port is not None and old_port != port:
        from ruamel.yaml import YAML
        yaml = YAML(typ='rt')
        yaml.preserve_quotes = True
        with open('templates/thingsboard.yml', 'r') as f:
            data = yaml.load(f)
        data['server']['port'] = "${HTTP_BIND_PORT:" + str(port) + "}"
        with open('/etc/thingsboard/conf/thingsboard.yml', 'w') as wf:
            yaml.dump(data, wf)
        close_port(old_port)
        open_port(port)
        service_restart('thingsboard')
