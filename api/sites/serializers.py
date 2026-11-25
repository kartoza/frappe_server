from django.conf import settings
from rest_framework import serializers

from .models import App, Backup, BenchApp, Job, Site, SiteApp, SiteDomain, is_hostname_allowed


class AppSerializer(serializers.ModelSerializer):
    class Meta:
        model = App
        fields = ('id', 'name', 'repo_url', 'actual_name', 'created_at')
        read_only_fields = ('actual_name',)


class SiteAppSerializer(serializers.ModelSerializer):
    app = AppSerializer(read_only=True)

    class Meta:
        model = SiteApp
        fields = ('id', 'app', 'installed_at')


class BenchAppSerializer(serializers.ModelSerializer):
    app = AppSerializer(read_only=True)

    class Meta:
        model = BenchApp
        fields = ('id', 'app', 'branch', 'fetched_at')


class SiteDomainSerializer(serializers.ModelSerializer):
    class Meta:
        model = SiteDomain
        fields = ('id', 'site', 'domain', 'is_default', 'created_at')
        read_only_fields = ('is_default', 'created_at')

    def validate_domain(self, value):
        """Same whitelist as a site's own hostname, plus global uniqueness.

        The model's own ``unique=True`` on ``domain`` already rules out a
        clash with another custom domain; this adds the cross-table check
        against every site's actual hostname, which that constraint can't see.
        """
        value = value.lower()
        if not is_hostname_allowed(value):
            allowed = ', '.join(settings.ALLOWED_SITE_DOMAINS)
            raise serializers.ValidationError(
                f'"{value}" is not under an allowed domain. Allowed: {allowed}.'
            )
        if Site.objects.filter(hostname=value).exists():
            raise serializers.ValidationError(f'"{value}" is already in use by another site.')
        return value


class SiteSerializer(serializers.ModelSerializer):
    installed_apps = SiteAppSerializer(many=True, read_only=True)
    domains = SiteDomainSerializer(many=True, read_only=True)
    default_domain = serializers.ReadOnlyField()
    app_ids = serializers.PrimaryKeyRelatedField(
        queryset=App.objects.all(), many=True, write_only=True, required=False
    )

    class Meta:
        model = Site
        fields = (
            'id', 'bench', 'hostname', 'status', 'maintenance_mode',
            'error_message', 'created_at', 'installed_apps', 'app_ids',
            'admin_password', 'domains', 'default_domain',
        )
        read_only_fields = (
            'status', 'maintenance_mode', 'error_message', 'created_at',
            'installed_apps', 'domains', 'default_domain',
        )
        extra_kwargs = {'admin_password': {'write_only': True}}

    def validate_hostname(self, value):
        """Reject hostnames outside the configured domain whitelist.

        nginx will happily route any hostname given to it — this is the
        actual gate against creating a site under a domain the operator
        doesn't control (see settings.ALLOWED_SITE_DOMAINS).
        """
        if not is_hostname_allowed(value):
            allowed = ', '.join(settings.ALLOWED_SITE_DOMAINS)
            raise serializers.ValidationError(
                f'"{value}" is not under an allowed domain. Allowed: {allowed}.'
            )
        if SiteDomain.objects.filter(domain=value.lower()).exists():
            raise serializers.ValidationError(f'"{value}" is already in use as another site\'s custom domain.')
        return value

    def validate(self, attrs):
        """Reject apps that aren't already fetched onto the target bench.

        Branch choice happens explicitly when fetching an app onto a
        bench (see BenchViewSet.fetch_app), not here — a site can only
        install what's already there.
        """
        bench = attrs.get('bench')
        app_ids = attrs.get('app_ids', [])
        if bench and app_ids:
            fetched_app_ids = set(
                BenchApp.objects.filter(bench=bench, app__in=app_ids).values_list('app_id', flat=True)
            )
            not_fetched = [app.name for app in app_ids if app.id not in fetched_app_ids]
            if not_fetched:
                raise serializers.ValidationError(
                    f'Not fetched on bench "{bench.name}" yet: {", ".join(not_fetched)}'
                )
        return attrs


class BackupSerializer(serializers.ModelSerializer):
    class Meta:
        model = Backup
        fields = ('id', 'site', 'file_path', 'size_bytes', 'status', 'created_at')
        read_only_fields = fields


class JobSerializer(serializers.ModelSerializer):
    class Meta:
        model = Job
        fields = (
            'id', 'job_type', 'bench', 'site', 'status', 'logs',
            'log_truncated', 'created_at', 'finished_at',
        )
        read_only_fields = fields
