from math import ceil
from datetime import datetime

from dns import name as dns_name
from dns.rdtypes.ANY import SOA
from dns.exception import DNSException

from django.core.validators import (
    MinValueValidator,
    MaxValueValidator,
)
from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.db import models, transaction
from django.db.models import Q, Max, ExpressionWrapper, BooleanField
from django.urls import reverse
from django.db.models.signals import m2m_changed
from django.dispatch import receiver
from django.conf import settings

from netbox.models import NetBoxModel
from netbox.search import SearchIndex, register_search
from netbox.plugins.utils import get_plugin_config
from utilities.querysets import RestrictedQuerySet
from utilities.choices import ChoiceSet
from ipam.models import IPAddress

from netbox_dns.fields import NetworkField, RFC2317NetworkField
from netbox_dns.utilities import (
    arpa_to_prefix,
    name_to_unicode,
    normalize_name,
    NameFormatError,
)
from netbox_dns.validators import (
    validate_fqdn,
    validate_domain_name,
)
from netbox_dns.mixins import ObjectModificationMixin

# +
# This is a hack designed to break cyclic imports between View, Record and Zone
# -
import netbox_dns.models.record as record
import netbox_dns.models.view as view
import netbox_dns.models.nameserver as nameserver


class ZoneManager(models.Manager.from_queryset(RestrictedQuerySet)):
    """
    Custom manager for zones providing the activity status annotation
    """

    def get_queryset(self):
        return (
            super()
            .get_queryset()
            .annotate(
                active=ExpressionWrapper(
                    Q(status__in=Zone.ACTIVE_STATUS_LIST), output_field=BooleanField()
                )
            )
        )


class ZoneStatusChoices(ChoiceSet):
    key = "Zone.status"

    STATUS_ACTIVE = "active"
    STATUS_RESERVED = "reserved"
    STATUS_DEPRECATED = "deprecated"
    STATUS_PARKED = "parked"

    CHOICES = [
        (STATUS_ACTIVE, "Active", "blue"),
        (STATUS_RESERVED, "Reserved", "cyan"),
        (STATUS_DEPRECATED, "Deprecated", "red"),
        (STATUS_PARKED, "Parked", "gray"),
    ]


class Zone(ObjectModificationMixin, NetBoxModel):
    ACTIVE_STATUS_LIST = (ZoneStatusChoices.STATUS_ACTIVE,)

    view = models.ForeignKey(
        to="View",
        on_delete=models.PROTECT,
        null=False,
    )
    name = models.CharField(
        max_length=255,
    )
    status = models.CharField(
        max_length=50,
        choices=ZoneStatusChoices,
        default=ZoneStatusChoices.STATUS_ACTIVE,
        blank=True,
    )
    nameservers = models.ManyToManyField(
        to="NameServer",
        related_name="zones",
        blank=True,
    )
    default_ttl = models.PositiveIntegerField(
        blank=True,
        verbose_name="Default TTL",
        validators=[MinValueValidator(1)],
    )
    soa_ttl = models.PositiveIntegerField(
        blank=False,
        null=False,
        verbose_name="SOA TTL",
        validators=[MinValueValidator(1)],
    )
    soa_mname = models.ForeignKey(
        to="NameServer",
        related_name="zones_soa",
        verbose_name="SOA MName",
        on_delete=models.PROTECT,
        blank=False,
        null=False,
    )
    soa_rname = models.CharField(
        max_length=255,
        blank=False,
        null=False,
        verbose_name="SOA RName",
    )
    soa_serial = models.BigIntegerField(
        blank=True,
        null=True,
        verbose_name="SOA Serial",
        validators=[MinValueValidator(1), MaxValueValidator(4294967295)],
    )
    soa_refresh = models.PositiveIntegerField(
        blank=False,
        null=False,
        verbose_name="SOA Refresh",
        validators=[MinValueValidator(1)],
    )
    soa_retry = models.PositiveIntegerField(
        blank=False,
        null=False,
        verbose_name="SOA Retry",
        validators=[MinValueValidator(1)],
    )
    soa_expire = models.PositiveIntegerField(
        blank=False,
        null=False,
        verbose_name="SOA Expire",
        validators=[MinValueValidator(1)],
    )
    soa_minimum = models.PositiveIntegerField(
        blank=False,
        null=False,
        verbose_name="SOA Minimum TTL",
        validators=[MinValueValidator(1)],
    )
    soa_serial_auto = models.BooleanField(
        verbose_name="Generate SOA Serial",
        help_text="Automatically generate the SOA Serial field",
        default=True,
    )
    description = models.CharField(
        max_length=200,
        blank=True,
    )
    arpa_network = NetworkField(
        verbose_name="ARPA Network",
        help_text="Network related to a reverse lookup zone (.arpa)",
        blank=True,
        null=True,
    )
    tenant = models.ForeignKey(
        to="tenancy.Tenant",
        on_delete=models.PROTECT,
        related_name="netbox_dns_zones",
        blank=True,
        null=True,
    )
    registrar = models.ForeignKey(
        to="Registrar",
        on_delete=models.SET_NULL,
        verbose_name="Registrar",
        help_text="The external registrar the domain is registered with",
        blank=True,
        null=True,
    )
    registry_domain_id = models.CharField(
        verbose_name="Registry Domain ID",
        help_text="The ID of the domain assigned by the registry",
        max_length=50,
        blank=True,
        null=True,
    )
    registrant = models.ForeignKey(
        to="Contact",
        on_delete=models.SET_NULL,
        verbose_name="Registrant",
        help_text="The owner of the domain",
        blank=True,
        null=True,
    )
    admin_c = models.ForeignKey(
        to="Contact",
        on_delete=models.SET_NULL,
        verbose_name="Admin Contact",
        related_name="admin_c_zones",
        help_text="The administrative contact for the domain",
        blank=True,
        null=True,
    )
    tech_c = models.ForeignKey(
        to="Contact",
        on_delete=models.SET_NULL,
        verbose_name="Tech Contact",
        related_name="tech_c_zones",
        help_text="The technical contact for the domain",
        blank=True,
        null=True,
    )
    billing_c = models.ForeignKey(
        to="Contact",
        on_delete=models.SET_NULL,
        verbose_name="Billing Contact",
        related_name="billing_c_zones",
        help_text="The billing contact for the domain",
        blank=True,
        null=True,
    )
    rfc2317_prefix = RFC2317NetworkField(
        verbose_name="RCF2317 Prefix",
        help_text="RFC2317 IPv4 prefix prefix with a length of at least 25 bits",
        blank=True,
        null=True,
    )
    rfc2317_parent_managed = models.BooleanField(
        verbose_name="RFC2317 Parent Managed",
        help_text="The parent zone for the RFC2317 zone is managed by NetBox DNS",
        default=False,
    )
    rfc2317_parent_zone = models.ForeignKey(
        to="self",
        on_delete=models.SET_NULL,
        verbose_name="RFC2317 Parent Zone",
        related_name="rfc2317_child_zones",
        help_text="Parent zone for RFC2317 reverse zones",
        blank=True,
        null=True,
    )

    soa_serial_dirty = False

    objects = ZoneManager()

    clone_fields = [
        "view",
        "name",
        "status",
        "nameservers",
        "default_ttl",
        "soa_ttl",
        "soa_mname",
        "soa_rname",
        "soa_refresh",
        "soa_retry",
        "soa_expire",
        "soa_minimum",
        "description",
    ]

    class Meta:
        ordering = (
            "view",
            "name",
        )
        unique_together = (
            "view",
            "name",
        )

    def __str__(self):
        if self.name == "." and get_plugin_config("netbox_dns", "enable_root_zones"):
            name = ". (root zone)"
        else:
            try:
                name = dns_name.from_text(self.name, origin=None).to_unicode()
            except dns_name.IDNAException:
                name = self.name

        if not self.view.default_view:
            return f"[{self.view}] {name}"

        return str(name)

    @staticmethod
    def get_defaults():
        return {
            field[5:]: value
            for field, value in settings.PLUGINS_CONFIG.get("netbox_dns").items()
            if field.startswith("zone_")
            and field not in ("zone_soa_mname", "zone_nameservers")
        }

    @property
    def display_name(self):
        return name_to_unicode(self.name)

    def get_status_color(self):
        return ZoneStatusChoices.colors.get(self.status)

    def get_absolute_url(self):
        return reverse("plugins:netbox_dns:zone", kwargs={"pk": self.pk})

    def get_status_class(self):
        return self.CSS_CLASSES.get(self.status)

    @property
    def is_active(self):
        return self.status in Zone.ACTIVE_STATUS_LIST

    @property
    def is_reverse_zone(self):
        return self.name.endswith(".arpa") and self.network_from_name is not None

    @property
    def is_rfc2317_zone(self):
        return self.rfc2317_prefix is not None

    def get_rfc2317_parent_zone(self):
        if not self.is_rfc2317_zone:
            return None

        return (
            Zone.objects.filter(
                view=self.view,
                arpa_network__net_contains=self.rfc2317_prefix,
            )
            .order_by("arpa_network__net_mask_length")
            .last()
        )

    @property
    def is_registered(self):
        return any(
            field is not None
            for field in (
                self.registrar,
                self.registry_domain_id,
                self.registrant,
                self.admin_c,
                self.tech_c,
                self.billing_c,
            )
        )

    def record_count(self, managed=False):
        return record.Record.objects.filter(zone=self, managed=managed).count()

    def rfc2317_child_zone_count(self):
        return Zone.objects.filter(rfc2317_parent_zone=self).count()

    def update_soa_record(self):
        soa_name = "@"
        soa_ttl = self.soa_ttl
        soa_rdata = SOA.SOA(
            rdclass=record.RecordClassChoices.IN,
            rdtype=record.RecordTypeChoices.SOA,
            mname=self.soa_mname.name,
            rname=self.soa_rname,
            serial=self.soa_serial,
            refresh=self.soa_refresh,
            retry=self.soa_retry,
            expire=self.soa_expire,
            minimum=self.soa_minimum,
        )

        try:
            soa_record = self.record_set.get(
                type=record.RecordTypeChoices.SOA, name=soa_name
            )

            if soa_record.ttl != soa_ttl or soa_record.value != soa_rdata.to_text():
                soa_record.ttl = soa_ttl
                soa_record.value = soa_rdata.to_text()
                soa_record.managed = True
                soa_record.save()

        except record.Record.DoesNotExist:
            record.Record.objects.create(
                zone_id=self.pk,
                type=record.RecordTypeChoices.SOA,
                name=soa_name,
                ttl=soa_ttl,
                value=soa_rdata.to_text(),
                managed=True,
            )

    def update_ns_records(self):
        ns_name = "@"

        nameservers = [f"{nameserver.name}." for nameserver in self.nameservers.all()]

        self.record_set.filter(type=record.RecordTypeChoices.NS, managed=True).exclude(
            value__in=nameservers
        ).delete()

        for ns in nameservers:
            record.Record.raw_objects.update_or_create(
                zone_id=self.pk,
                type=record.RecordTypeChoices.NS,
                name=ns_name,
                value=ns,
                managed=True,
            )

    def check_nameservers(self):
        nameservers = self.nameservers.all()

        ns_warnings = []
        ns_errors = []

        if not nameservers:
            ns_errors.append(f"No nameservers are configured for zone {self}")

        for _nameserver in nameservers:
            name = dns_name.from_text(_nameserver.name, origin=None)
            parent = name.parent()

            if len(parent) < 2:
                continue

            try:
                ns_zone = Zone.objects.get(view_id=self.view.pk, name=parent.to_text())
            except ObjectDoesNotExist:
                continue

            relative_name = name.relativize(parent).to_text()
            address_records = record.Record.objects.filter(
                Q(zone=ns_zone),
                Q(status__in=record.Record.ACTIVE_STATUS_LIST),
                Q(Q(name=f"{_nameserver.name}.") | Q(name=relative_name)),
                Q(
                    Q(type=record.RecordTypeChoices.A)
                    | Q(type=record.RecordTypeChoices.AAAA)
                ),
            )

            if not address_records:
                ns_warnings.append(
                    f"Nameserver {_nameserver.name} does not have an active address record in zone {ns_zone}"
                )

        return ns_warnings, ns_errors

    def check_soa_serial_increment(self, old_serial, new_serial):
        MAX_SOA_SERIAL_INCREMENT = 2**31 - 1
        SOA_SERIAL_WRAP = 2**32

        if old_serial is None:
            return

        if (new_serial - old_serial) % SOA_SERIAL_WRAP > MAX_SOA_SERIAL_INCREMENT:
            raise ValidationError(
                {"soa_serial": f"soa_serial must not decrease for zone {self.name}."}
            )

    def get_auto_serial(self):
        records = record.Record.objects.filter(zone_id=self.pk).exclude(
            type=record.RecordTypeChoices.SOA
        )
        if records:
            soa_serial = (
                records.aggregate(Max("last_updated"))
                .get("last_updated__max")
                .timestamp()
            )
        else:
            soa_serial = ceil(datetime.now().timestamp())

        if self.last_updated:
            soa_serial = ceil(max(soa_serial, self.last_updated.timestamp()))

        return soa_serial

    def update_serial(self, save_zone_serial=True):
        if not self.soa_serial_auto:
            return

        self.last_updated = datetime.now()
        self.soa_serial = ceil(datetime.now().timestamp())

        if save_zone_serial:
            super().save(update_fields=["soa_serial", "last_updated"])
            self.soa_serial_dirty = False
            self.update_soa_record()
        else:
            self.soa_serial_dirty = True

    def save_soa_serial(self):
        if self.soa_serial_auto and self.soa_serial_dirty:
            super().save(update_fields=["soa_serial", "last_updated"])
            self.soa_serial_dirty = False

    @property
    def network_from_name(self):
        return arpa_to_prefix(self.name)

    def update_rfc2317_parent_zone(self):
        if not self.is_rfc2317_zone:
            return

        if self.rfc2317_parent_managed:
            rfc2317_parent_zone = self.get_rfc2317_parent_zone()

            if rfc2317_parent_zone is None:
                self.rfc2317_parent_managed = False
                self.rfc2317_parent_zone = None
                self.save(
                    update_fields=["rfc2317_parent_zone", "rfc2317_parent_managed"]
                )

            elif self.rfc2317_parent_zone != rfc2317_parent_zone:
                self.rfc2317_parent_zone = rfc2317_parent_zone
                self.save(update_fields=["rfc2317_parent_zone"])

        ptr_records = self.record_set.filter(
            type=record.RecordTypeChoices.PTR
        ).prefetch_related("zone", "rfc2317_cname_record")
        ptr_zones = {ptr_record.zone for ptr_record in ptr_records}

        if self.rfc2317_parent_managed:
            for ptr_record in ptr_records:
                ptr_record.save(save_zone_serial=False)

            self.rfc2317_parent_zone.save_soa_serial()
            self.rfc2317_parent_zone.update_soa_record()
        else:
            cname_records = {
                ptr_record.rfc2317_cname_record
                for ptr_record in ptr_records
                if ptr_record.rfc2317_cname_record is not None
            }
            cname_zones = {cname_record.zone for cname_record in cname_records}

            for ptr_record in ptr_records:
                ptr_record.save(update_rfc2317_cname=False, save_zone_serial=False)
            for cname_record in cname_records:
                cname_record.delete(save_zone_serial=False)

            for cname_zone in cname_zones:
                cname_zone.save_soa_serial()
                cname_zone.update_soa_record()

        for ptr_zone in ptr_zones:
            ptr_zone.save_soa_serial()
            ptr_zone.update_soa_record()

    def clean_fields(self, exclude=None):
        defaults = settings.PLUGINS_CONFIG.get("netbox_dns")

        if self.view_id is None:
            self.view_id = view.View.get_default_view().pk

        for field, value in self.get_defaults().items():
            if getattr(self, field) in (None, ""):
                if value not in (None, ""):
                    setattr(self, field, value)

        if self.soa_mname_id is None:
            default_soa_mname = defaults.get("zone_soa_mname")
            try:
                self.soa_mname = nameserver.NameServer.objects.get(
                    name=default_soa_mname
                )
            except nameserver.NameServer.DoesNotExist:
                raise ValidationError(
                    f"Default soa_mname instance {default_soa_mname} does not exist"
                )

        super().clean_fields(exclude=exclude)

    def clean(self, *args, **kwargs):
        if self.soa_ttl is None:
            self.soa_ttl = self.default_ttl

        try:
            self.name = normalize_name(self.name)
        except NameFormatError as exc:
            raise ValidationError(
                {
                    "name": str(exc),
                }
            ) from None

        try:
            validate_domain_name(self.name)
        except ValidationError as exc:
            raise ValidationError(
                {
                    "name": exc,
                }
            ) from None

        if self.soa_rname in (None, ""):
            raise ValidationError("soa_rname not set and no default value defined")
        try:
            dns_name.from_text(self.soa_rname, origin=dns_name.root)
            validate_fqdn(self.soa_rname)
        except (DNSException, ValidationError) as exc:
            raise ValidationError(
                {
                    "soa_rname": exc,
                }
            ) from None

        if not self.soa_serial_auto:
            if self.soa_serial is None:
                raise ValidationError(
                    {
                        "soa_serial": f"soa_serial is not defined and soa_serial_auto is disabled for zone {self.name}."
                    }
                )

        if self.pk is not None:
            old_zone = Zone.objects.get(pk=self.pk)
            if not self.soa_serial_auto:
                self.check_soa_serial_increment(old_zone.soa_serial, self.soa_serial)
            else:
                try:
                    self.check_soa_serial_increment(
                        old_zone.soa_serial, self.get_auto_serial()
                    )
                except ValidationError:
                    raise ValidationError(
                        {
                            "soa_serial_auto": f"Enabling soa_serial_auto would decrease soa_serial for zone {self.name}."
                        }
                    )

        if self.is_reverse_zone:
            self.arpa_network = self.network_from_name

        if self.is_rfc2317_zone:
            if self.arpa_network is not None:
                raise ValidationError(
                    {
                        "rfc2317_prefix": "A regular reverse zone can not be used as an RFC2317 zone."
                    }
                )

            if self.rfc2317_parent_managed:
                rfc2317_parent_zone = self.get_rfc2317_parent_zone()

                if rfc2317_parent_zone is None:
                    raise ValidationError(
                        {
                            "rfc2317_parent_managed": f"Parent zone not found in view {self.view}."
                        }
                    )

                self.rfc2317_parent_zone = rfc2317_parent_zone
            else:
                self.rfc2317_parent_zone = None

            overlapping_zones = Zone.objects.filter(
                view=self.view,
                rfc2317_prefix__net_overlap=self.rfc2317_prefix,
                active=True,
            ).exclude(pk=self.pk)

            if overlapping_zones.exists():
                raise ValidationError(
                    {
                        "rfc2317_prefix": f"RFC2317 prefix overlaps with zone {overlapping_zones.first()}."
                    }
                )

        else:
            self.rfc2317_parent_managed = False
            self.rfc2317_parent_zone = None

        super().clean(*args, **kwargs)

    def save(self, *args, **kwargs):
        self.full_clean()

        new_zone = self.pk is None
        if not new_zone:
            old_zone = Zone.objects.get(pk=self.pk)

        name_changed = not new_zone and old_zone.name != self.name
        view_changed = not new_zone and old_zone.view != self.view
        status_changed = not new_zone and old_zone.status != self.status
        rfc2317_changed = not new_zone and (
            old_zone.rfc2317_prefix != self.rfc2317_prefix
            or old_zone.rfc2317_parent_managed != self.rfc2317_parent_managed
        )

        if self.soa_serial_auto:
            self.soa_serial = self.get_auto_serial()

        super().save(*args, **kwargs)

        if (
            new_zone or name_changed or view_changed or status_changed
        ) and self.is_reverse_zone:
            zones = Zone.objects.filter(
                view=self.view,
                arpa_network__net_contains_or_equals=self.arpa_network,
            )
            address_records = record.Record.objects.filter(
                Q(ptr_record__isnull=True) | Q(ptr_record__zone__in=zones),
                type__in=(
                    record.RecordTypeChoices.A,
                    record.RecordTypeChoices.AAAA,
                ),
                disable_ptr=False,
            )

            for address_record in address_records:
                address_record.save(
                    update_fields=["ptr_record"], save_zone_serial=False
                )

            for zone in zones:
                zone.save_soa_serial()

            if self.arpa_network.version == 4:
                rfc2317_child_zones = Zone.objects.filter(
                    rfc2317_prefix__net_contained=self.arpa_network,
                    rfc2317_parent_managed=True,
                )
                for child_zone in rfc2317_child_zones:
                    child_zone.update_rfc2317_parent_zone()

        if (
            new_zone
            or name_changed
            or view_changed
            or status_changed
            or rfc2317_changed
        ) and self.is_rfc2317_zone:
            zones = Zone.objects.filter(
                view=self.view,
                arpa_network__net_contains=self.rfc2317_prefix,
            )
            address_records = record.Record.objects.filter(
                Q(ptr_record__isnull=True)
                | Q(ptr_record__zone__in=zones)
                | Q(ptr_record__zone=self),
                type=record.RecordTypeChoices.A,
                disable_ptr=False,
            )

            for address_record in address_records:
                address_record.save(
                    update_fields=["ptr_record"],
                    update_rfc2317_cname=False,
                    save_zone_serial=False,
                )

            for zone in zones:
                zone.save_soa_serial()

            self.update_rfc2317_parent_zone()

        elif name_changed or view_changed or status_changed:
            for address_record in self.record_set.filter(
                type__in=(record.RecordTypeChoices.A, record.RecordTypeChoices.AAAA)
            ):
                address_record.save(update_fields=["ptr_record"])

            # Fix name in IP Address when zone name is changed
            if (
                get_plugin_config("netbox_dns", "feature_ipam_coupling")
                and name_changed
            ):
                for ip in IPAddress.objects.filter(
                    custom_field_data__ipaddress_dns_zone_id=self.pk
                ):
                    ip.dns_name = f'{ip.custom_field_data["ipaddress_dns_record_name"]}.{self.name}'
                    ip.save(update_fields=["dns_name"])

        if name_changed:
            for _record in self.record_set.all():
                _record.save(
                    update_fields=["fqdn"],
                    save_zone_serial=False,
                    update_rrset_ttl=False,
                    update_rfc2317_cname=False,
                )

        self.save_soa_serial()
        self.update_soa_record()

    def delete(self, *args, **kwargs):
        with transaction.atomic():
            address_records = self.record_set.filter(
                ptr_record__isnull=False
            ).prefetch_related("ptr_record")
            for address_record in address_records:
                address_record.ptr_record.delete()

            ptr_records = self.record_set.filter(address_record__isnull=False)
            update_records = [
                address_record.pk
                for address_record in record.Record.objects.filter(
                    ptr_record__in=ptr_records
                )
            ]

            cname_records = {
                ptr_record.rfc2317_cname_record
                for ptr_record in ptr_records
                if ptr_record.rfc2317_cname_record is not None
            }
            cname_zones = {cname_record.zone for cname_record in cname_records}

            for cname_record in cname_records:
                cname_record.delete(save_zone_serial=False)
            for cname_zone in cname_zones:
                cname_zone.save_soa_serial()
                cname_zone.update_soa_record()

            rfc2317_child_zones = [
                child_zone.pk for child_zone in self.rfc2317_child_zones.all()
            ]

            if get_plugin_config("netbox_dns", "feature_ipam_coupling"):
                for ip in IPAddress.objects.filter(
                    custom_field_data__ipaddress_dns_zone_id=self.pk
                ):
                    ip.dns_name = ""
                    ip.custom_field_data["ipaddress_dns_record_name"] = None
                    ip.custom_field_data["ipaddress_dns_zone_id"] = None
                    ip.save(update_fields=["dns_name", "custom_field_data"])

            super().delete(*args, **kwargs)

        address_records = record.Record.objects.filter(
            pk__in=update_records
        ).prefetch_related("zone")

        for address_record in address_records:
            address_record.save(save_zone_serial=False)
        for address_zone in {address_record.zone for address_record in address_records}:
            address_zone.save_soa_serial()
            address_zone.update_soa_record()

        rfc2317_child_zones = Zone.objects.filter(pk__in=rfc2317_child_zones)
        if rfc2317_child_zones:
            for child_zone in rfc2317_child_zones:
                child_zone.update_rfc2317_parent_zone()

            new_rfc2317_parent_zone = rfc2317_child_zones.first().rfc2317_parent_zone
            if new_rfc2317_parent_zone is not None:
                new_rfc2317_parent_zone.save_soa_serial()
                new_rfc2317_parent_zone.update_soa_record()


@receiver(m2m_changed, sender=Zone.nameservers.through)
def update_ns_records(**kwargs):
    if kwargs.get("action") not in ["post_add", "post_remove"]:
        return

    zone = kwargs.get("instance")
    zone.update_ns_records()


@register_search
class ZoneIndex(SearchIndex):
    model = Zone
    fields = (
        ("name", 100),
        ("view", 150),
        ("registrar", 300),
        ("registry_domain_id", 300),
        ("registrant", 300),
        ("admin_c", 300),
        ("tech_c", 300),
        ("billing_c", 300),
        ("description", 500),
        ("soa_rname", 1000),
        ("soa_mname", 1000),
        ("arpa_network", 1000),
    )
