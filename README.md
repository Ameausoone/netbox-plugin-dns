# NetBox DNS
The NetBox DNS plugin enables NetBox to manage operational DNS data such as name servers, zones, records and views, as well as registration data for domains. It can automate tasks like creating PTR records, generating zone serial numbers, NS and SOA records, as well as validate names and values values for resource records to ensure zone data is consistent, current and conforming to the relevant RFCs.

<div align="center">
<a href="https://pypi.org/project/netbox-plugin-dns/"><img src="https://img.shields.io/pypi/v/netbox-plugin-dns" alt="PyPi"/></a>
<a href="https://github.com/peteeckel/netbox-plugin-dns/stargazers"><img src="https://img.shields.io/github/stars/peteeckel/netbox-plugin-dns?style=flat" alt="Stars Badge"/></a>
<a href="https://github.com/peteeckel/netbox-plugin-dns/network/members"><img src="https://img.shields.io/github/forks/peteeckel/netbox-plugin-dns?style=flat" alt="Forks Badge"/></a>
<a href="https://github.com/peteeckel/netbox-plugin-dns/issues"><img src="https://img.shields.io/github/issues/peteeckel/netbox-plugin-dns" alt="Issues Badge"/></a>
<a href="https://github.com/peteeckel/netbox-plugin-dns/pulls"><img src="https://img.shields.io/github/issues-pr/peteeckel/netbox-plugin-dns" alt="Pull Requests Badge"/></a>
<a href="https://github.com/peteeckel/netbox-plugin-dns/graphs/contributors"><img alt="GitHub contributors" src="https://img.shields.io/github/contributors/peteeckel/netbox-plugin-dns?color=2b9348"></a>
<a href="https://github.com/peteeckel/netbox-plugin-dns/blob/master/LICENSE"><img src="https://img.shields.io/github/license/peteeckel/netbox-plugin-dns?color=2b9348" alt="License Badge"/></a>
<a href="https://pepy.tech/project/netbox-plugin-dns"><img alt="Downloads" src="https://static.pepy.tech/badge/netbox-plugin-dns"></a>
<a href="https://pepy.tech/project/netbox-plugin-dns"><img alt="Downloads/Week" src="https://static.pepy.tech/badge/netbox-plugin-dns/month"></a>
<a href="https://pepy.tech/project/netbox-plugin-dns"><img alt="Downloads/Month" src="https://static.pepy.tech/badge/netbox-plugin-dns/week"></a>
</div>

## Objectives
NetBox DNS is designed to be the 'DNS Source of Truth' analogous to NetBox being the 'Network Source of Truth'.

The plugin holds information about DNS name servers, DNS Views and Zones, and DNS Records, making it a data source for automatically provisioning DNS instances. Registrarion information about DNS registrars and contacts for DNS domains can also be stored and associated with zones.

The main focus of the plugin is to ensure the quality of data stored in it. To achieve this, many validation and automation mechanisms are in place:

* Validation of record names and values
* Automatic maintenance of PTR records for IPv6 and IPv4 address records
* Automatic generation of SOA records, optionally including the serial number of the zone data
* Validation of record types such as CNAME and singletons, to ensure DNS zone validity
* [RFC 2317](https://datatracker.ietf.org/doc/html/rfc2317) support for delegation of PTR zones for IPv4 subnets longer than 24 bits

Other main features are:

* Support for BIND views, providing lightweight namespaces for zones
* Support for IDN, inclucing the validation of punycode names
* Fully supported NetBox REST and GraphQL API
* Support for all major NetBox features like Global Search, Tenancy, Change Logs, Tagging, Journaling etc.

## Non-Objectives
In the same way in which NetBox is not a network management application, NetBox DNS does not provide functionality to manage specific name servers or DNS service providers or generate input like configuration and zone files for them. The focus is on the completeness and integrity of the data needed to run DNS zones, not on the peculiarities of a plethora of servers and services actually using the data. This functionality is left to specialized integration tools, or in many cases it can be easily implemented using Ansible or similar tools based on NetBox DNS data. Example code for some simple use cases is provided.

For integration with a large number of DNS server implementations integration tools like [octodns-netbox-dns](https://pypi.org/project/octodns-netbox-dns/) are available.

## Requirements

* NetBox 4.0.0 or higher
* Python 3.10 or higher

## Installation & Configuration

### Installation

```
$ source /opt/netbox/venv/bin/activate
(venv) $ pip install netbox-plugin-dns
```

### NetBox Configuration

Add the plugin to the NetBox config. `~/netbox/configuration.py`

```python
PLUGINS = [
    "netbox_dns",
]
```

To permanently keep the plugin installed when updating NetBox via `update.sh`:

```
echo netbox-plugin-dns >> ~/netbox/local_requirements.txt
```

To add the required netbox_dns tables to your database run the following command from your NetBox directory:

```
./manage.py migrate
```

Full documentation on using plugins with NetBox: [Using Plugins - NetBox Documentation](https://netbox.readthedocs.io/en/stable/plugins/)

## Contribute

Contributions are always welcome! Please see the [Contribution Guidelines](CONTRIBUTING.md)

## Documentation

For further information, please refer to the full documentation: [Using NetBox DNS](docs/using_netbox_dns.md)

## License

MIT
