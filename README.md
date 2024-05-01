# Ansible Module: Gitlab Runner Registration

This Ansible module provides a simple way to manage Gitlab Runner registration on a Linux system. The module can register, re-register and unregister from a given Gitlab Runner parameters.

## Important

The module uses new Gitlab Runner registration architecture. More details at [Gitlab Docs](https://docs.gitlab.com/ee/architecture/blueprints/runner_tokens/index.html#using-the-authentication-token-in-place-of-the-registration-token). It uses runner authentication token, **NOT** registration token which is deprecated.

At present the module doesn't support changing configuration without re-registration.
One of the reasons is because of Gitlab Runner. It thinks that managing config file by gitlab-runner service itself is a good idea. So when we try to manage runner instance we should keep in mind that service can add parameter at some points to configuration file. This idioma brings us to problem. How can we manage some runner idepotently with ansible? This module manages runner with some limitations with no overcomplication of module code, without bashsible and with no yaml programming though...

## Configuring Gitlab Runner

The following methods are supported:

- Basic parameters could be configured with module opts.
- Environ vars(see full list with 'gitlab-runner register --help')
- Skeleton config file. See [Gitlab Runner Template](https://docs.gitlab.com/runner/register/#register-with-a-configuration-template) for details.

Choose any method you like or even all of them at same time. More examples are bellow.

If you need to change some config parameters you will have to re-register the instance.
This could be done with `recreate` parameter. Also re-registration occures **automatically** when authentication token changed.

## Installation

This module require `toml` python module at target host. Beyond that no additional installation steps are required. Just place it to your [Ansible libs](https://docs.ansible.com/ansible/latest/reference_appendices/config.html#default-module-path) directory.

## Usage Examples

Simplest example:

```yaml
- name: Simple register runner
  gitlab_runner_register:
    api_url: "{{ gitlab_url }}"
    token: "{{ gitlab_runner_token }}"
    name: "{{ ansible_hostname }}"
    executor: "docker"
    default_image: "alpine:latest"
```

You could overwrite default global params of Gitlab Runner:

```yaml
- name: Simple register runner with global params
  gitlab_runner_register:
    api_url: "{{ gitlab_url }}"
    token: "{{ gitlab_runner_token }}"
    name: "{{ ansible_hostname }}"
    executor: "docker"
    default_image: "alpine:latest"
    global_params:
      concurrent: 10
      check_interval: 60
      connection_max_age: "1m"
      session_server:
        session_timeout: 180
```

Re-register the runner as simple as adding only one additional parameter `recreate`:

```yaml
- name: Re-register runner
  gitlab_runner_register:
    api_url: "{{ gitlab_url }}"
    token: "{{ gitlab_runner_token }}"
    name: "{{ ansible_hostname }}"
    executor: "docker"
    default_image: "alpine:latest"
    recreate: true
  tags: register
```

Or it's quite convenient to just run playbook with extra-vars: `-t register -e recreate=true`

Now bellow example that uses environs:

```yaml
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
```

And you can use template:

```yaml
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
```
