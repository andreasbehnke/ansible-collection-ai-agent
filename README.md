# ansible-collection-ai-agent

Ansible Role Collection for provisioning AI Agents.

Fully qualified collection name: `andreasbehnke.ai_agent`

## Layout

* `galaxy.yml` - collection metadata (namespace, name, version)
* `meta/runtime.yml` - minimum supported ansible version
* `roles/` - the roles of this collection:
  * [`signal_cli`](roles/signal_cli/README.md) - signal-cli and its JSON-RPC/REST daemon,
    running as the unprivileged `signal` user on the loopback interface

## Adding a role

Create `roles/<role_name>/` using the standard role layout (`tasks/`, `defaults/`,
`handlers/`, `vars/`, `meta/`). Role names must be lowercase with underscores, since
they become part of the fully qualified name `andreasbehnke.ai_agent.<role_name>`.

## Usage

```yaml
- hosts: agent
  become: yes
  roles:
    - andreasbehnke.ai_agent.<role_name>
```

The collection is consumed by the infrastructure repository `platform`: it is listed in
`ansible/collections.txt` and cloned into `ansible/collections/ansible_collections/` by
`ansible/setup.sh`.
