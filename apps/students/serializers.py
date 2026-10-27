from rest_framework import serializers

from apps.corecode.models import StudentClass
from apps.students.models import Student


class StudentSyncSerializer(serializers.ModelSerializer):
    """Serializer for syncing students from an external system."""

    current_class = serializers.CharField(required=False, allow_blank=True, allow_null=True)

    class Meta:
        model = Student
        fields = (
            "registration_number",
            "surname",
            "firstname",
            "other_name",
            "gender",
            "date_of_birth",
            "current_class",
            "date_of_admission",
            "parent_mobile_number",
            "address",
            "others",
        )
        extra_kwargs = {
            "registration_number": {"required": True},
            "surname": {"required": True},
            "firstname": {"required": True},
        }

    def validate_current_class(self, value):
        if value in (None, ""):
            return None
        return value

    def validate(self, attrs):
        registration_number = attrs.get("registration_number")
        if not registration_number:
            raise serializers.ValidationError(
                {"registration_number": "This field is required."}
            )

        surname = attrs.get("surname")
        if not surname:
            raise serializers.ValidationError(
                {"surname": "This field is required."}
            )

        firstname = attrs.get("firstname")
        if not firstname:
            raise serializers.ValidationError(
                {"firstname": "This field is required."}
            )

        return attrs

    def create(self, validated_data):
        current_class_name = validated_data.pop("current_class", None)
        student, _ = Student.objects.update_or_create(
            registration_number=validated_data.get("registration_number"),
            defaults={
                "surname": validated_data.get("surname"),
                "firstname": validated_data.get("firstname"),
                "other_name": validated_data.get("other_name", ""),
                "gender": validated_data.get("gender", "male"),
                "date_of_birth": validated_data.get("date_of_birth"),
                "date_of_admission": validated_data.get("date_of_admission"),
                "parent_mobile_number": validated_data.get("parent_mobile_number", ""),
                "address": validated_data.get("address", ""),
                "others": validated_data.get("others", ""),
            },
        )

        if current_class_name:
            class_obj, created = StudentClass.objects.get_or_create(
                name=current_class_name
            )
            student.current_class = class_obj
            student.save(update_fields=["current_class"])

        return student

    def update(self, instance, validated_data):
        current_class_name = validated_data.pop("current_class", None)

        for attr, value in validated_data.items():
            setattr(instance, attr, value)

        if current_class_name:
            class_obj, _ = StudentClass.objects.get_or_create(name=current_class_name)
            instance.current_class = class_obj

        instance.save()
        return instance
