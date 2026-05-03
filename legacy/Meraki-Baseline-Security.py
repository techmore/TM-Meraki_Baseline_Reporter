import requests
from prettytable import PrettyTable

# Table for network and security settings
security_table = PrettyTable()
security_table.field_names = ["Organization", "Network", "License Edition", "Anti-Malware", "Intrusion Prevention", "Spoof Protection", "Open Ports From INT"]
security_table.align["Organization"] = "l"
security_table.align["Network"] = "l"
security_table.align["License Edition"] = "l"
security_table.align["Anti-Malware"] = "l"
security_table.align["Intrusion Prevention"] = "l"
security_table.align["Spoof Protection"] = "l"
security_table.align["Open Ports From INT"] = "r"

# Table for admin details
admin_table = PrettyTable()
admin_table.field_names = ["Admin Name", "Email", "Organization", "Network", "Access Level", "2FA Enabled", "Last Active"]
admin_table.align["Admin Name"] = "l"
admin_table.align["Email"] = "l"
admin_table.align["Organization"] = "l"
admin_table.align["Network"] = "l"
admin_table.align["Access Level"] = "l"
admin_table.align["2FA Enabled"] = "l"
admin_table.align["Last Active"] = "l"

# API endpoint for organizations
org_url = 'https://api.meraki.com/api/v1/organizations'
fw_url = 'https://api.meraki.com/api/v1/networks/{}/appliance/'

# Read the file containing the API keys and org IDs
with open("api_keys_org_ids.txt", "r") as f:
    lines = f.readlines()

# Loop through each line in the file
for line in lines:
    api_key, org_id = line.strip().split(",")

    # Get list of networks for the selected organization
    networks_url = f"https://api.meraki.com/api/v1/organizations/{org_id}/networks"
    response = requests.get(networks_url, headers={'X-Cisco-Meraki-API-Key': api_key})
    networks = response.json()

    # Filter the networks to find MX firewalls
    mx_firewalls = [network for network in networks if network['productTypes'][0] == 'appliance']

    # Get the organization name
    org_response = requests.get(f"{org_url}/{org_id}", headers={'X-Cisco-Meraki-API-Key': api_key})
    org = org_response.json()
    org_name = org['name']
    
    # Retrieve the list of admins for the organization
    admins_url = f"{org_url}/{org_id}/admins"
    admins_response = requests.get(admins_url, headers={'X-Cisco-Meraki-API-Key': api_key}).json()
    
    # Populate admin table with details
    for admin in admins_response:
        assigned_networks = admin.get("networks", [])
        if not assigned_networks:
            # If admin has no specific network assignment, add a single row with organization-level access
            admin_table.add_row([
                admin['name'],
                admin['email'],
                org_name,
                "Organization-wide",
                admin['orgAccess'],
                "Yes" if admin.get("twoFactorAuthEnabled") else "No",
                admin.get("lastActive", "N/A")
            ])
        else:
            # Add a row for each network the admin is assigned to
            for network in assigned_networks:
                network_name = next((net['name'] for net in networks if net['id'] == network['id']), "N/A")
                admin_table.add_row([
                    admin['name'],
                    admin['email'],
                    org_name,
                    network_name,
                    network['access'],
                    "Yes" if admin.get("twoFactorAuthEnabled") else "No",
                    admin.get("lastActive", "N/A")
                ])

    # Loop through the firewall list
    for firewall in mx_firewalls:
        network_name = firewall['name']
        licenses_response = requests.get(f"{org_url}/{org_id}/licensing/coterm/licenses/", headers={'X-Cisco-Meraki-API-Key': api_key}).json()
        license_edition = "Enterprise"
        
        for item in licenses_response:
            counts = item.get('counts')
            for count in counts:
                if count.get('model').startswith('MX'):
                    editions = item.get('editions')
                    for edition in editions:
                        if 'Advanced Security' in edition.get('edition'):
                            license_edition = 'Advanced Security'
                        break

        # Fetch security settings
        fw_url_antimalware = (fw_url + "security//malware")
        firewall_settings = requests.get(fw_url_antimalware.format(firewall['id']), headers={'X-Cisco-Meraki-API-Key': api_key}).json()
        anti_malware_enabled = firewall_settings['mode']

        fw_url_intrusion = (fw_url + "security//intrusion")
        firewall_settings = requests.get(fw_url_intrusion.format(firewall['id']), headers={'X-Cisco-Meraki-API-Key': api_key}).json()
        intrusion_prevention_enabled = firewall_settings.get('mode', "Not Supported")

        fw_url_spoof = (fw_url + "firewall//settings")
        firewall_settings = requests.get(fw_url_spoof.format(firewall['id']), headers={'X-Cisco-Meraki-API-Key': api_key}).json()
        spoof_protection_enabled = firewall_settings['spoofingProtection']['ipSourceGuard']['mode']

        fw_url_port_forwarding = (fw_url + "firewall//portForwardingRules")
        firewall_rules = requests.get(fw_url_port_forwarding.format(firewall['id']), headers={'X-Cisco-Meraki-API-Key': api_key}).json()
        open_ports = ', '.join(str(rule['publicPort']) for rule in firewall_rules.get('rules', []) if rule['allowedIps'] == ['any']) or 'None'
        
        security_table.add_row([org_name, network_name, license_edition, anti_malware_enabled, intrusion_prevention_enabled, spoof_protection_enabled, open_ports])

# Print the tables
print(security_table)
print("\nAdmin Details:")
print(admin_table)
