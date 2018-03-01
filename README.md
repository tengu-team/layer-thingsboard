# Overview

[ThingsBoard](https://thingsboard.io/) is an open-source IoT platform for data
visualization. It allows you to monitor and control your IoT devices.

# Usage

This charm can deploy a single standalone ThingsBoard unit that uses PostgreSQL
as external database.

To setup a single standalone service:

```bash
juju deploy cs:~tengu-team/thingsboard-0
juju deploy postgresql
juju add-relation thingsboard postgresql:db
```

# Contact Information

## Authors

 - Dixan Peña Peña <dixan.pena@tengu.io>
