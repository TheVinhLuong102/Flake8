"""Module containing the main mecurial hook interface and helpers.

.. autofunction:: hook
.. autofunction:: install

"""
import configparser
import os
import subprocess

from flake8 import exceptions as exc

__all__ = ('hook', 'install')


def hook(ui, repo, **kwargs):
    """Execute Flake8 on the repository provided by Mercurial.

    To understand the parameters read more of the Mercurial documentation
    around Hooks: https://www.mercurial-scm.org/wiki/Hook.

    We avoid using the ``ui`` attribute because it can cause issues with
    the GPL license tha Mercurial is under. We don't import it, but we
    avoid using it all the same.
    """
    from flake8.main import application
    hgrc = find_hgrc(create_if_missing=False)
    if hgrc is None:
        print('Cannot locate your root mercurial repository.')
        raise SystemExit(True)

    hgconfig = configparser_for(hgrc)
    strict = hgconfig.get('flake8', 'strict', fallback=True)

    app = application.Application()
    app.run()

    if strict:
        return app.result_count
    return 0


def install():
    """Ensure that the mercurial hooks are installed."""
    hgrc = find_hgrc(create_if_missing=True)
    if hgrc is None:
        print('Could not locate your root mercurial repository.')
        raise SystemExit(True)

    hgconfig = configparser_for(hgrc)

    if not hgconfig.has_section('hooks'):
        hgconfig.add_section('hooks')

    if hgconfig.has_option('hooks', 'commit'):
        raise exc.MercurialCommitHookAlreadyExists(
            path=hgrc,
            value=hgconfig.get('hooks', 'commit'),
        )

    if hgconfig.has_option('hooks', 'qrefresh'):
        raise exc.MercurialQRefreshHookAlreadyExists(
            path=hgrc,
            value=hgconfig.get('hooks', 'qrefresh'),
        )

    hgconfig.set('hooks', 'commit', 'python:flake8.main.mercurial.hook')
    hgconfig.set('hooks', 'qrefresh', 'python:flake8.main.mercurial.hook')

    if not hgconfig.has_section('flake8'):
        hgconfig.add_section('flake8')

    if not hgconfig.has_option('flake8', 'strict'):
        hgconfig.set('flake8', 'strict', False)

    with open(hgrc, 'w') as fd:
        hgconfig.write(fd)


def find_hgrc(create_if_missing=False):
    root = subprocess.Popen(
        ['hg', 'root'],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    (hg_directory, _) = root.communicate()
    if callable(getattr(hg_directory, 'decode', None)):
        hg_directory = hg_directory.decode('utf-8')

    if not os.path.isdir(hg_directory):
        return None

    hgrc = os.path.abspath(
        os.path.join(hg_directory, '.hg', 'hgrc')
    )
    if not os.path.exists(hgrc):
        if create_if_missing:
            open(hgrc, 'w').close()
        else:
            return None

    return hgrc


def configparser_for(path):
    parser = configparser.ConfigParser(interpolation=None)
    parser.read(path)
    return parser
