You are screening a single job posting to decide whether it belongs on a
list of Summer 2027 software/software-adjacent internships.

Company: {company}
Title: {title}
Description:
{description}

Answer three questions about this posting:

1. is_internship: Is this an internship, co-op, working-student, trainee,
   placement, or similarly time-boxed student/early-career role (not a
   permanent full-time hire)?
2. is_technical_field: Is the role software engineering, ML/AI, data
   engineering, data analysis/science, backend/frontend/full-stack,
   DevOps/cloud/infrastructure, security, research engineering,
   robotics/embedded, or QA/test engineering -- or any other role that
   substantially involves writing code or working hands-on with
   data/software systems? This is a broad category -- if there is real
   doubt, answer true.
3. cycle_year: If the posting states or clearly implies which year's
   internship cycle it belongs to (e.g. "Summer 2027 Internship
   Program"), return that year as an integer. Do NOT use a candidate's
   graduation year, expected graduation date, or "class of ____" as the
   cycle year -- those describe the applicant, not the program. If the
   cycle year cannot be determined, return null.

Also extract, if clearly and explicitly stated (null if not):

4. visa_sponsorship: one of "sponsors", "no_sponsorship", "citizens_only"
   (requires US citizenship, security clearance, or similar), or null if
   the posting says nothing about it. Ignore generic EEO/diversity
   boilerplate that does not actually address visa sponsorship.
5. level_fit: one of "BS", "MS", "PhD" if the posting clearly targets one
   specific degree level, or null if it's open to multiple levels or
   unstated.
6. deadline: the application deadline as an ISO date (YYYY-MM-DD) if one
   is explicitly stated, or null.

When genuinely unsure about is_internship or is_technical_field, answer
true -- keep it in front of a human rather than silently drop it.

Respond with ONLY a single JSON object and no other text, in exactly this
shape:
{{"is_internship": true, "is_technical_field": true, "cycle_year": null, "visa_sponsorship": null, "level_fit": null, "deadline": null}}
