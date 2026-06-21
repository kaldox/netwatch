# Contributing to NetWatch

Thanks for your interest! A few notes:

## Adding router support

The most valuable contribution is support for non-FritzBox routers. The
interface is small — implement a `RouterProvider` in `src/router.py` that
returns a `RouterLineStatus`. The FritzBox provider is the reference.

What's useful to expose, in priority order:
1. Downstream/upstream sync rate
2. Physical max attainable rate
3. SNR margin / attenuation
4. Connection drops / line errors

## Running tests

```bash
pip install -r requirements.txt
python -m pytest tests/ -v
```

## Style

- Keep the monitoring loop non-blocking; slow work (speed tests, router
  logins) belongs on its own timer/thread.
- Every schema change needs a forward-only migration in `database.py`.
- Don't break the "rule out the measuring device" guarantee — keep resource
  sampling intact.

## Privacy

Never commit real credentials, IPs, or captured data. `config/config.yaml`,
the database, logs and reports are git-ignored for this reason.
