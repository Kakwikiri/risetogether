from datetime import date, datetime


MINIMUM_AGE = 13
MAXIMUM_AGE = 120
POST_AGE_RATINGS = {"general", "adult"}


def age_on_date(birth_date, today=None):
    if not birth_date:
        return None
    today = today or date.today()
    return today.year - birth_date.year - (
        (today.month, today.day) < (birth_date.month, birth_date.day)
    )


def parse_birth_date(value, today=None):
    try:
        birth_date = datetime.strptime((value or "").strip(), "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None, "Enter a valid date of birth."
    age = age_on_date(birth_date, today)
    if age is None or age < MINIMUM_AGE:
        return None, f"You must be at least {MINIMUM_AGE} to use RiseTogether."
    if age > MAXIMUM_AGE:
        return None, "Enter a valid date of birth."
    return birth_date, ""


def user_age(user, today=None):
    profile = getattr(user, "profile", None) if user else None
    return age_on_date(getattr(profile, "birth_date", None), today)


def user_is_adult(user, today=None):
    age = user_age(user, today)
    return age is not None and age >= 18


def user_age_group(user, today=None):
    age = user_age(user, today)
    if age is None:
        return "unknown"
    if age < 18:
        return "teen"
    if age < 25:
        return "young_adult"
    if age < 40:
        return "adult"
    if age < 60:
        return "midlife"
    return "older_adult"


def can_view_age_rating(user, rating):
    return rating != "adult" or user_is_adult(user)
