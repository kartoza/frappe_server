from rest_framework import serializers

from .models import App, Backup, BenchApp, Job, Site, SiteApp


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


class SiteSerializer(serializers.ModelSerializer):
    installed_apps = SiteAppSerializer(many=True, read_only=True)
    app_ids = serializers.PrimaryKeyRelatedField(
        queryset=App.objects.all(), many=True, write_only=True, required=False
    )

    class Meta:
        model = Site
        fields = (
            'id', 'bench', 'hostname', 'status', 'maintenance_mode',
            'error_message', 'created_at', 'installed_apps', 'app_ids',
            'admin_password',
        )
        read_only_fields = (
            'status', 'maintenance_mode', 'error_message', 'created_at',
            'installed_apps',
        )
        extra_kwargs = {'admin_password': {'write_only': True}}

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
