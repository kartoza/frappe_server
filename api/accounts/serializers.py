"""Serializers for user management, invites, and self-service profile actions."""
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from rest_framework import serializers

from .models import Invite

User = get_user_model()


class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = (
            'id', 'username', 'email', 'first_name', 'last_name',
            'is_staff', 'is_active', 'date_joined', 'last_login',
        )
        read_only_fields = ('id', 'username', 'email', 'date_joined', 'last_login')


class InviteSerializer(serializers.ModelSerializer):
    status = serializers.ReadOnlyField()
    invited_by_email = serializers.CharField(source='invited_by.email', read_only=True, default='')

    class Meta:
        model = Invite
        fields = (
            'id', 'email', 'is_admin', 'status', 'token',
            'invited_by_email', 'created_at', 'accepted_at',
        )
        read_only_fields = ('id', 'status', 'token', 'invited_by_email', 'created_at', 'accepted_at')


class CreateInviteSerializer(serializers.Serializer):
    email = serializers.EmailField()
    is_admin = serializers.BooleanField(default=False)

    def validate_email(self, value):
        value = value.lower()
        if User.objects.filter(username=value).exists():
            raise serializers.ValidationError('A user with this email already exists.')
        return value


class AcceptInviteSerializer(serializers.Serializer):
    password = serializers.CharField(write_only=True)

    def validate_password(self, value):
        validate_password(value)
        return value


class ChangePasswordSerializer(serializers.Serializer):
    current_password = serializers.CharField(write_only=True)
    new_password = serializers.CharField(write_only=True)

    def validate_new_password(self, value):
        validate_password(value)
        return value
