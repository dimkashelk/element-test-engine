"""Canonical Docker digest references, preserving the configured content pin."""


def digest_reference(image):
    repository, at, digest = image.partition('@')
    if at and ':' in repository.rsplit('/', 1)[-1]:
        repository = repository.rsplit(':', 1)[0]
    return repository + at + digest
