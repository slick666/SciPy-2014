from os.path import join as pjoin
import datetime

from fabric.api import run, env, sudo, put, cd, local, task
from fabric.contrib.files import sed, upload_template
from fabtools import require, supervisor
import fabtools
import jinja2


env.disable_known_hosts = True

VENV_DIR = '/home/scipy/venvs/'
REPO = '/home/scipy/site/SciPy-2014'
SITE_PATH = '/home/scipy/site'
GIT_REPO = 'https://github.com/scipy-conference/SciPy-2014.git'

USAGE = """
The command to deploy is:
    fab $TARGET $COMMAND

Where $TARGET is staging || prod || dev (eventually), and $COMMAND
is the task to be run.  $TARGET is a special fabric task that correctly
sets up the environment

Example usage:
$ fab staging deploy
$ fab prod deploy:576a8e4241962464f4ac9c11cd5054e306f2f0d1
$ fab dev deploy:origin/v1.0003
"""

CONFIG = None


staging_config = {
    'site': 'citationsneeded.org',
    'upstream': 'citationsneeded_org',
    'available': 'citationsneeded',
    'cert_name': 'citationsneeded',
    'hosts': ['mrterry@citationsneeded.org'],
    'local_settings': 'deployment/staging_settings.py',
}


prod_config = {
    'site': 'conference.scipy.org',
    'upstream': 'conference_scipy_org',
    'available': 'conference',
    'cert_name': 'scipy2014',
    'hosts': ['162.242.221.143'],
    'local_settings': 'deployment/prod_settings.py',
}


dev_config = {
}


@task
def staging():
    global CONFIG
    CONFIG = staging_config
    env.hosts = CONFIG['hosts']


@task
def prod():
    global CONFIG
    CONFIG = staging_config
    env.hosts = CONFIG['hosts']


def dev():
    global CONFIG
    CONFIG = dev_config
    env.key_filename = local(
        'vagrant ssh-CONFIG | grep IdentityFile | cut -f4 -d " "',
        capture=True,
    )


def scipy_do(*args, **kw):
    kw['user'] = 'scipy'
    return sudo(*args, **kw)


@task
def deploy(commit=None):
    install_dependencies()

    update_repo(commit=commit)

    venv_path = deploy_venv()
    deploy_mail(venv_path)
    build_static(venv_path)

    deploy_supervisor()
    restart_gunicorn()

    deploy_nginx()
    restart_nginx()


@task
def update_repo(commit=None):
    if commit is None:
        commit = 'origin/master'

    with cd(REPO):
        scipy_do('git fetch')
        scipy_do('git checkout %s' % commit)

    scipy_put(CONFIG['local_settings'],
              pjoin(REPO, 'scipy2014/local_settings.py'))
    scipy_do('cp ~/secrets.py %s' % pjoin(REPO, 'scipy2014', 'secrets.py'))


def build_static(venv_path):
    activate_cmd = 'source %s' % pjoin(VENV_DIR, venv_path, 'bin/activate')
    collect_cmd = 'python manage.py collectstatic --noinput --clear'
    with cd(REPO):
        scipy_do(activate_cmd + ' && ' + collect_cmd)
    scipy_do('chmod -R a+rx %s' % pjoin(SITE_PATH, 'site_media'))


@task
def deploy_nginx():
    render_to_file('deployment/nginx_conf_template', 'nginx_conf',
                   server_name=CONFIG['site'],
                   cert_name=CONFIG['cert_name'],
                   upstream=CONFIG['upstream'])
    put('nginx_conf',
        pjoin('/etc/nginx/sites-available/', CONFIG['avaailable']),
        use_sudo=True)
    require.nginx.enabled(CONFIG['avaailable'])
    require.nginx.disabled('default')
    #install_certs()


@task
def deploy_supervisor():
    upload_template('deployment/scipy2014.conf',
                    '/etc/supervisor/conf.d/scipy2014.conf',
                    use_sudo=True)
    supervisor.update_config()


def build_venv():
    with cd(REPO):
        commit = run('git rev-parse HEAD').strip()
    venv_path = pjoin(VENV_DIR, commit)
    activate = 'source %s' % pjoin(venv_path, 'bin/activate')
    install = 'pip install -r %s' % pjoin(REPO, 'requirements.txt')

    dirs = run('ls %s' % VENV_DIR).split()
    if commit not in dirs:
        print "Virtual env for commit doesn't exist.  Creating."
        scipy_do('virtualenv %s' % venv_path)
        scipy_do(activate + ' && ' + install)
    else:
        print 'Virtual env exists.'

    today = datetime.date.today().isoformat()
    new_venvs = [d for d in dirs if d.startswith(today)]
    new_venvs.sort()
    human_path = today + '.%i' % len(new_venvs)
    with cd(VENV_DIR):
        scipy_do('ln -s %s %s' % (commit, human_path))
    return pjoin(VENV_DIR, human_path)


def deploy_venv():
    venv_path = build_venv()
    put_gunicorn_conf(venv_path)
    return venv_path


@task
def put_gunicorn_conf(venv):
    render_to_file('deployment/runserver_template.sh',
                   'runserver.sh',
                   virtualenv=venv)
    scipy_put('runserver.sh', pjoin(SITE_PATH, 'bin/runserver.sh'),
              mode='0755')


@task
def restart_gunicorn():
    sudo('supervisorctl restart scipy2014')


@task
def restart_nginx():
    sudo('service nginx restart')


def scipy_put(local_path, dest_path, **kw):
    kw['use_sudo'] = True
    put(local_path, dest_path, **kw)
    sudo('chown scipy %s' % dest_path)


def render_to_file(template_path, output_path, **kw):
    with open(template_path) as f:
        template = jinja2.Template(f.read())
    conf = template.render(**kw)
    with open(output_path, 'w') as f:
        f.write(conf)


#def install_certs():
#    put('citationsneeded.crt', '/etc/ssl/certs/citationsneeded.crt',
#        use_sudo=True, mode=0400)
#    put('citationsneeded.key', '/etc/ssl/private/citationsneeded.key',
#        use_sudo=True, mode=0400)


@task
def deploy_mail(venv_path):
    render_to_file('deployment/django_mail_template.sh', 'django_mail.sh',
                   virtualenv=venv_path)
    scipy_put('django_mail.sh', pjoin(SITE_PATH, 'bin/django_mail.sh'))


@task
def provision():
    install_dependencies()
    #install_python_packages()
    configure_ssh()
    setup_user()
    setup_sitepaths()


def setup_user():
    sudo('useradd -s/bin/bash -d/home/scipy -m scipy')


def configure_ssh():
    sed('/etc/ssh/sshd_config',
        '^#PasswordAuthentication yes',
        'PasswordAuthentication no',
        use_sudo=True)
    sudo('service ssh restart')


def setup_sitepaths():
    with cd(fabtools.user.home_directory('scipy')):
        scipy_do('mkdir -p site venvs')
    with cd(SITE_PATH):
        scipy_do('mkdir -p bin logs')
        scipy_do('git clone %s' % GIT_REPO)


def install_dependencies():
    require.deb.uptodate_index(max_age={'hour': 1})
    require.deb.packages([
        'python-software-properties',
        'python-dev',
        'build-essential',
        'nginx',
        'libxslt1-dev',
        'supervisor',
        'git',
        'tig',
        'vim',
        'multitail',
        'curl',
        'tmux',
        'htop',
        'ack-grep',
        'libmysqlclient-dev',
        'mysql-server',
        'mysql-client',
        'python-mysqldb',
        'libjpeg-dev',
        'libtiff-dev',
        'zlib1g-dev',
        'python-virtualenv',
    ])


def install_python_packages():
    sudo('wget https://bitbucket.org/pypa/setuptools/raw/bootstrap/ez_setup.py')
    sudo('wget https://raw.github.com/pypa/pip/master/contrib/get-pip.py')
    sudo('python ez_setup.py')
    sudo('python get-pip.py')
    # install global python packages
    require.python.packages(['virtualenvwrapper', 'setproctitle'], use_sudo=True)
