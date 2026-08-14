import os
import requests
import time

class AsanaUpdater:
    def __init__(self, token=None):
        self.token = token or os.environ.get('ASANA_PERSONAL_ACCESS_TOKEN') or os.environ.get('ASANA_PAT')
        if not self.token:
            try:
                import json
                with open('ASANAconfig.json', 'r') as f:
                    cfg = json.load(f)
                self.token = cfg.get('ASANA_PERSONAL_ACCESS_TOKEN') or cfg.get('ASANA_PAT')
            except Exception:
                self.token = None

        if not self.token:
            raise RuntimeError('ASANA token not found; set ASANA_PERSONAL_ACCESS_TOKEN or ASANAconfig.json')

        self.base = 'https://app.asana.com/api/1.0'
        self.headers = {'Authorization': f'Bearer {self.token}', 'Content-Type': 'application/json'}

    def _request_with_retry(self, method, url, **kwargs):
        max_retries = 10
        for attempt in range(max_retries):
            r = requests.request(method, url, headers=self.headers, **kwargs)
            if r.status_code == 429:
                wait_time = int(r.headers.get('Retry-After', 60))
                print(f"    [WARN] Asana rate limit (429) hit - pausing for {wait_time}s (attempt {attempt+1}/{max_retries})")
                time.sleep(wait_time)
                continue
            r.raise_for_status()
            return r
        r.raise_for_status()
        return r

    def get_task(self, task_gid, opt_fields='custom_fields,completed'):
        url = f"{self.base}/tasks/{task_gid}?opt_fields={opt_fields}"
        r = self._request_with_retry('GET', url)
        return r.json().get('data', {})

    def update_task(self, task_gid, completed=None, custom_fields=None):
        payload = {'data': {}}
        if completed is not None:
            payload['data']['completed'] = bool(completed)
        if custom_fields is not None:
            payload['data']['custom_fields'] = {str(k): v for k, v in custom_fields.items()}
        url = f"{self.base}/tasks/{task_gid}"
        r = self._request_with_retry('PUT', url, json=payload)
        return r.json().get('data', {})

    def get_custom_field(self, field_gid):
        url = f"{self.base}/custom_fields/{field_gid}"
        r = self._request_with_retry('GET', url)
        return r.json().get('data', {})

    def set_enum_field_by_label(self, task_gid, field_gid, label):
        field = self.get_custom_field(field_gid)
        options = field.get('enum_options') or []
        match = None
        for opt in options:
            if opt.get('name', '').strip().lower() == label.strip().lower():
                match = opt.get('gid')
                break
        if not match:
            raise ValueError(f"Could not find enum option '{label}' for field {field_gid}")
        return self.update_task(task_gid, custom_fields={field_gid: match})

    def create_task(self, project_gid, name, notes='', custom_fields=None, assignee=None):
        payload = {'data': {'name': name, 'projects': [project_gid]}}
        if notes:
            payload['data']['notes'] = notes
        if custom_fields:
            payload['data']['custom_fields'] = {str(k): v for k, v in custom_fields.items()}
        if assignee:
            payload['data']['assignee'] = assignee
        r = self._request_with_retry('POST', f"{self.base}/tasks", json=payload)
        return r.json().get('data', {})

    def create_subtask(self, parent_gid, name, notes='', custom_fields=None, assignee=None):
        payload = {'data': {'name': name}}
        if notes:
            payload['data']['notes'] = notes
        if custom_fields:
            payload['data']['custom_fields'] = {str(k): v for k, v in custom_fields.items()}
        if assignee:
            payload['data']['assignee'] = assignee
        r = self._request_with_retry('POST', f"{self.base}/tasks/{parent_gid}/subtasks", json=payload)
        return r.json().get('data', {})

    def add_to_project(self, task_gid, project_gid):
        self._request_with_retry('POST', f"{self.base}/tasks/{task_gid}/addProject",
                                 json={'data': {'project': project_gid}})

    @staticmethod
    def extract_custom_field_value(task, field_gid):
        for cf in task.get('custom_fields', []):
            if str(cf.get('gid')) == str(field_gid):
                if cf.get('number_value') is not None:
                    return cf.get('number_value')
                if cf.get('display_value') is not None:
                    return cf.get('display_value')
                return cf.get('text_value')
        return None
