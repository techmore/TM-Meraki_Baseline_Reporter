#!/bin/sh

python3 -c "import requests; api_key = 'REPLACEME'; orgs = requests.get('https://api.meraki.com/api/v1/organizations', headers={'X-Cisco-Meraki-API-Key': api_key, 'Content-Type': 'application/json'}).json(); print('\n'.join([f'ID: {org['id']}, Name: {org['name']}' for org in orgs]))"
