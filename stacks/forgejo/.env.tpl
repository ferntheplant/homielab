# Resolved by `op run` in stacks/manage.sh. Values live in 1Password: lil-homie/forgejo
# SECRET_KEY encrypts the push-mirror SSH keys and 2FA secrets in the database. If it is lost, the
# mirrors stop. Never change it on a running instance.
FORGEJO_SECRET_KEY=op://homielab/forgejo/SECRET_KEY
FORGEJO_INTERNAL_TOKEN=op://homielab/forgejo/INTERNAL_TOKEN
