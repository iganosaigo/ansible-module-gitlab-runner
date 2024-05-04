from __future__ import absolute_import, division, print_function

__metaclass__ = type

DOCUMENTATION = r"""
---
module: gitlab_runner_register
author:
  - Alexander Grigoriev (@iganosaigo)
short_description: Configure Gitlab Runner
description:
  - Register, unregister and re-register the Gitlab Runner instance.
  - For now if you need to change some config parameters
    you will have to re-register the instance.
  - Also if you change the token then re-registration occurs as well.
  - Note that runner authentication token is used.
    NOT registration token which is deprecated. See bellow.
  - Module uses new Gitlab Runner registration architecture.
    More details at U(https://docs.gitlab.com/ee/architecture/blueprints/runner_tokens/index.html#using-the-authentication-token-in-place-of-the-registration-token)
  - Instance could be configured with cli(i.e. module opts),
    ENVs(see full list with 'gitlab-runner register --help')
    and template file U(https://docs.gitlab.com/runner/register/#register-with-a-configuration-template).
    Choose any you like or even all of them at same time.
  - A valid Gitlab Runner authentication token is required for all operations.
notes:
  - There is conceptual problem with Gitlab Runner.
    It thinks that managing config file by gitlab-runner service itself is
    a good idea. So when we trying to manage runner instance we should keep in
    mind that service can add parameter at some points to configuration file.
    This idioma brings us to another problem. How we can manage that runner
    idepotently with ansible? This module manages runner with some limitations
    with no overcomplication of module code, without bashsible
    and with no yaml programming though...

requirements:
  - python-toml

attributes:
  check_mode:
    support: none
  diff_mode:
    support: none

options:
  api_url:
    description:
      - API URL of Gitlab Server
    required: true
    type: str
  state:
    description:
      - Desired state of the provided Runner instance.
    required: false
    default: present
    choices: ["present", "absent"]
    type: str
  name:
    description:
      - Name of Runner instance
    required: true
    type: str
  executor:
    description:
      - Runner executor mode.
      - If not specified you MUST specify value whether in template_file or whithin ENVs
    required: false
    type: str
  default_image:
    description:
      - Default image of Runner instance.
    required: false
    type: str
  global_params:
    description:
      - Options that could be set to override default globals of config file
      - Applied only on instance registration. To change existing runner reregister required.
      - You could find valid params at Gitlab Runner documentation.
    required: false
    type: dict
  environ_vars:
    description:
      - You could set those env params to build config while registering instance.
      - Applied only on instance registration. To change existing runner reregister required.
      - You could view all values running 'gitlab-runner register --help' command.
    required: false
    type: dict
  template_file:
    description:
      - Place that template to build config while registering instance.
      - Applied only on instance registration. To change existing runner reregister required.
      - You could find out how to make it at gitlab runner documentation.
    required: false
    type: str
  recreate:
    description:
      - Force recreate Runner instance with specified params.
      - If current instance already registered then it will unregister and register again with specified params.
    required: false
    default: false
    type: bool
"""

EXAMPLES = r"""

- name: Simple register runner
  gitlab_runner_register:
    api_url: "{{ gitlab_url }}"
    token: "{{ gitlab_runner_token }}"
    name: "{{ ansible_hostname }}"
    executor: "docker"
    default_image: "alpine:latest"

- name: Simple register runner with global params
  gitlab_runner_register:
    api_url: "{{ gitlab_url }}"
    token: "{{ gitlab_runner_token }}"
    name: "{{ ansible_hostname }}"
    executor: "docker"
    default_image: "alpine:latest"
    global_params:
      concurrent: 10
      check_interval: 30
      connection_max_age: "15m"
      session_server:
        session_timeout: 1800

- name: Re-register runner
  gitlab_runner_register:
    api_url: "{{ gitlab_url }}"
    token: "{{ gitlab_runner_token }}"
    name: "{{ ansible_hostname }}"
    executor: "docker"
    default_image: "alpine:latest"
    recreate: true

- name: Register runner with environments params
  gitlab_runner_register:
    api_url: "{{ gitlab_url }}"
    token: "{{ gitlab_runner_token }}"
    name: "{{ ansible_hostname }}"
    environ_vars:
      RUNNER_EXECUTOR: docker
      DOCKER_CPUSET_CPUS: "1-2"
      DOCKER_PRIVILEGED: true
      DOCKER_MEMORY: 2000
      DOCKER_IMAGE: "alpine:latest"

# Install template file and then run runner registration
- name: Template Config
  ansible.builtin.copy:
    content: |
      [[runners]]
        [runners.docker]
          tls_verify = false
          image = "ruby:2.7"
          cpuset_cpus = "0"
          memory = "100"
    dest: /tmp/temp.toml

- name: Register Runner with template config and global params
  gitlab_runner_register:
    api_url: "{{ gitlab_url }}"
    token: "{{ gitlab_runner_token }}"
    name: "{{ ansible_hostname }}"
    executor: docker
    template_file: "/tmp/temp.toml"
    global_params:
      concurrent: 10
      check_interval: 30
      connection_max_age: "15m"
      session_server:
        session_timeout: 1800
"""

RETURN = r"""
changed:
  description: Return changed for aix_filesystems actions as true or false.
  returned: always
  type: bool
runner_state:
  description: Return current state of Runner
  returned: always
  type: str
msg:
  description: Action done with reregistration
  returned: when registering fist time, unregistering or reregistering
  type: str
"""
import os
import re
import tempfile
import traceback
from enum import Enum

from ansible.module_utils.basic import AnsibleModule, missing_required_lib
from ansible.module_utils.common.text.converters import to_text

TOML_IMP_ERR = None
try:
    import toml

    HAS_TOML = True
except ImportError:
    TOML_IMP_ERR = traceback.format_exc()
    HAS_TOML = False

RUNNER_CONFIG = "/etc/gitlab-runner/config.toml"
RUNNER_ID = "/etc/gitlab-runner/.runner_system_id"


class RunnerState(Enum):
    REREGISTERED = "Reregistered"
    REGISTERED = "Registered"
    UNREGISTERED = "Unregistered"
    TOKEN_MISMATCH = "Token Mismatch"
    NAME_MISMATCH = "Runner Name Mismatch"


class Runner(object):
    def __init__(self, module: AnsibleModule):
        self.module = module

        self.state = self.module.params["state"]
        self.api_url = self.module.params["api_url"]
        self.token = self.module.params["token"]
        self.name = self.module.params["name"]
        self.executor = self.module.params.get("executor")
        self.default_image = self.module.params["default_image"]
        self.environ_vars = self.module.params.get("environ_vars")
        self.global_params = self.module.params.get("global_params")
        self.template_file = self.module.params.get("template_file")
        self.recreate = self.module.params.get("recreate")
        self.warnings = []
        self.current_name = None

        self.command_results = {}

        self.bin = self.get_binary()

    def load_config_content(self):
        """
        Get current Gitlab Runner configuration content.
        Return data as Dict.
        """
        return toml.load(RUNNER_CONFIG)

    def make_start_config(self):
        """
        Global variables can't be managed with binary by ENVs or template file.
        Before registering instance global variables modified here.
        """
        if self.global_params:
            tmpfd, tmpfile = tempfile.mkstemp(dir=self.module.tmpdir)
            with os.fdopen(tmpfd, "w") as f:
                toml.dump(self.global_params, f)
            self.module.atomic_move(tmpfile, RUNNER_CONFIG)

    def verify_config_exists(self):
        """
        Assumed that base config file and runner_id file are
        already added by gitlab-runner service itself.
        """
        try:
            os.stat(RUNNER_ID)
        except FileNotFoundError:
            # We assume that gitlab-runner after start creates config
            self.module.fail_json(
                msg="Runner didn't initialize it's runner_id.",
            )

        try:
            os.stat(RUNNER_CONFIG)
        except FileNotFoundError:
            return False

        return True

    def get_state(self, config: dict):
        """
        Simple mapping of different gitlab-runner states
        """
        try:
            runner_section = config["runners"][0]
            runner_name = runner_section["name"]
            runner_token = runner_section["token"]
        except (IndexError, KeyError):
            return RunnerState.UNREGISTERED
        if runner_token != self.token:
            return RunnerState.TOKEN_MISMATCH
        if runner_name != self.name:
            miss_state = RunnerState.NAME_MISMATCH.value
            self.warnings.append(
                f"{miss_state}. Perhaps re-registration required.",
            )
            self.current_name = runner_name

        return RunnerState.REGISTERED

    def register_runner(self):
        """
        Register Gitlab Runner. It runs binary in non-interactive mode.
        If template file provided then add template path to command
        in addition to cli parameters.
        """
        cmd = [self.bin, "register", "--non-interactive"]
        cmd.extend(["--url", self.api_url])
        cmd.extend(["--token", self.token])
        cmd.extend(["--name", self.name])
        if self.executor:
            cmd.extend(["--executor", self.executor])
        if self.default_image:
            cmd.extend(["--docker-image", self.default_image])
        if self.template_file:
            cmd.extend(["--template-config", self.template_file])

        # Prepare environment variables
        environment = None
        if self.environ_vars:
            environment = {}
            for k, v in self.environ_vars.items():
                environment[k] = to_text(v)

        rc, stdout, stderr = self.module.run_command(
            cmd,
            environ_update=environment,
        )
        if rc != 0:
            self.module.fail_json(
                msg="Error while registering runner",
                stdout=stdout,
                stderr=stderr,
            )

    def unregister_runner(self):
        """
        Unregister Gitlab Runner instance.
        """
        cmd = [self.bin, "unregister"]
        name = self.name
        if self.current_name:
            name = self.current_name
        cmd.extend(["--name", name])
        rc, stdout, stderr = self.module.run_command(cmd)
        if rc != 0:
            self.module.fail_json(
                msg="Error while unregistering runner",
                stdout=stdout,
                stderr=stderr,
            )

    def get_binary(self):
        """
        Get binary path of gitlab-runner.
        """
        return self.module.get_bin_path("gitlab-runner", required=True)

    def check_service(self):
        """
        Verify that gitlab-runner service is running
        """
        cmd = [self.bin, "status"]
        rc, stdout, stderr = self.module.run_command(cmd)
        if rc != 0:
            if re.search(r"Service has stopped", stderr, re.MULTILINE):
                self.module.fail_json(msg="Service(systemd) not running.")
            else:
                self.module.fail_json(
                    "gitlab-runner can't get status",
                    stdout=stdout,
                    stderr=stderr,
                )

        if not re.search("Service is running", stdout, re.MULTILINE):
            self.module.fail_json(
                msg="Gitlab-Runner service not running! Ensure service is up.",
                stdout=stdout,
                stderr=stderr,
            )

    def do_disable(self):
        "Unregister Gitlab Runner."
        self.unregister_runner()

    def do_enable(self):
        """
        Create basic minimal config file with global variables.
        Run registration of Gitlab Runner.
        """
        self.make_start_config()
        self.register_runner()

    def do_reenable(self):
        """
        Recreate Gitlab Runner instance.
        It creates basic minimal config file and
        run registration of Gitlab Runner.
        """
        self.unregister_runner()
        self.do_enable()

    def act(self):
        """
        Main logic entrypoint.
        """
        state_after = None
        self.check_service()

        config_exists = self.verify_config_exists()

        if config_exists:
            config_content = self.load_config_content()
            state_before = self.get_state(config_content)
        else:
            state_before = RunnerState.UNREGISTERED

        if self.state == "present":
            if state_before == RunnerState.UNREGISTERED:
                self.do_enable()
                state_after = RunnerState.REGISTERED
                self.command_results["msg"] = "Init registering Runner"

            elif state_before == RunnerState.TOKEN_MISMATCH:
                self.do_reenable()
                state_after = RunnerState.REREGISTERED
                self.command_results["msg"] = (
                    "Reregistering Runner due to Token mismatch"
                )

            elif state_before == RunnerState.REGISTERED:
                if self.recreate:
                    self.do_reenable()
                    state_after = RunnerState.REREGISTERED
                    self.command_results["msg"] = (
                        "Force reregistering Runner due to recreate option"
                    )

        elif self.state == "absent":
            if state_before == RunnerState.REGISTERED:
                self.do_disable()
                state_after = RunnerState.UNREGISTERED
                self.command_results["msg"] = "Unregistering Runner"

        if state_after:
            self.command_results["runner_state"] = state_after.value
        else:
            self.command_results["runner_state"] = state_before.value

        if self.warnings:
            self.command_results["warnings"] = self.warnings

        self.command_results["changed"] = (
            state_before.value != self.command_results["runner_state"]
        )

        self.module.exit_json(**self.command_results)


def main():
    module = setup_module_object()
    if not HAS_TOML:
        module.fail_json(
            msg=missing_required_lib("toml"),
            exception=TOML_IMP_ERR,
        )

    gitlab_runner = Runner(module)
    gitlab_runner.act()


def get_default_globals():
    params = {
        "concurrent": 1,
        "check_interval": 0,
        "connection_max_age": "15m0s",
        "shutdown_timeout": 0,
        "session_server": {
            "session_timeout": 1800,
        },
    }
    return params


def make_argument_spec():
    spec = dict(
        api_url=dict(required=True, type="str"),
        state=dict(
            choices=["present", "absent"],
            default="present",
        ),
        token=dict(required=True, type="str", no_log=True),
        name=dict(required=True, type="str"),
        executor=dict(type="str"),
        default_image=dict(type="str"),
        global_params=dict(type="dict", default=get_default_globals()),
        environ_vars=dict(type="dict"),
        template_file=dict(type="str"),
        recreate=dict(type="bool", default=False),
    )
    return spec


def setup_module_object():
    module = AnsibleModule(
        argument_spec=make_argument_spec(),
        supports_check_mode=False,
        required_one_of=[
            [
                "executor",
                "template_file",
                "environ_vars",
            ],
        ],
    )
    return module


if __name__ == "__main__":
    main()
