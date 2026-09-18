from careeros.core.models import Goals, Profile


def job_query_from_profile(profile: Profile, goals: Goals) -> str:
    parts = []
    if profile.title:
        parts.append(profile.title)
    keywords = []
    for item in goals.short_term + goals.long_term:
        for word in item.split():
            if word not in keywords:
                keywords.append(word)
    parts.extend(keywords[:3])
    query = " ".join(parts)
    return query[:80]
