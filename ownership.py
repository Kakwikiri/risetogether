import os


def platform_owner_username():
    return (os.getenv("PLATFORM_SUPER_ADMIN_USERNAME") or "kakwikiri").strip().lower()


def is_platform_owner_username(username):
    return bool(username) and username.strip().lower() == platform_owner_username()


def is_platform_owner(user):
    return bool(user and is_platform_owner_username(getattr(user, "username", "")))
