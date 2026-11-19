from rest_framework import serializers

from sites.serializers import BenchAppSerializer

from .models import Bench, Server


class ServerSerializer(serializers.ModelSerializer):
    class Meta:
        model = Server
        fields = ('id', 'name', 'created_at')


class BenchSerializer(serializers.ModelSerializer):
    fetched_apps = BenchAppSerializer(many=True, read_only=True)

    class Meta:
        model = Bench
        fields = (
            'id',
            'server',
            'name',
            'frappe_branch',
            'status',
            'error_message',
            'created_at',
            'fetched_apps',
        )
        read_only_fields = (
            'server', 'status', 'error_message', 'created_at', 'fetched_apps',
        )
