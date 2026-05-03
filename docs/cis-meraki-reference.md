# CIS Controls Mapping To Cisco Meraki

Reference material adapted from `iramku/Meraki-Security-Benchmark:Meraki-Security-Audit`.
This file is kept as context for future report improvements; it is not an active pipeline entrypoint.

## CIS Control 1: Inventory And Control Of Enterprise Assets

Meraki Dashboard and Systems Manager provide visibility into connected devices, including wired and wireless clients.
Network Access Control through Meraki access policies can help control unauthorized devices.

## CIS Control 2: Inventory And Control Of Software Assets

Meraki Systems Manager can support software inventory management on enrolled devices.
Application allow/block policies can enforce which applications may be installed or run.

## CIS Control 3: Data Protection

Meraki Auto VPN and site-to-site VPN encrypt data in transit.
Group policies, firewall rules, and Layer 7 controls can restrict sensitive access and reduce data exfiltration paths.

## CIS Control 4: Secure Configuration Of Enterprise Assets And Software

Meraki MX security appliances and cloud-managed firmware workflows support secure configuration and update management.
Firmware planning should still be reviewed operationally because automatic updates do not replace change control.

## CIS Control 5: Account Management

Meraki Dashboard SSO, MFA, and role-based access control help secure administrator access.
Dashboard administrator inventory and privilege review should be part of routine security assessment.

## CIS Control 6: Access Control Management

Identity-based access policies such as 802.1X and RADIUS integration can control network access by user or device identity.
Layer 7 firewall rules and group policies can further restrict access to enterprise assets.

## CIS Control 7: Continuous Vulnerability Management

Meraki security and health telemetry can identify vulnerable configurations and operational risks.
MX Advanced Security features such as malware protection can block known malicious traffic when licensed and enabled.

## CIS Control 8: Audit Log Management

Meraki Dashboard event logs, syslog export, alerts, and notifications support centralized security monitoring.
Assessment should confirm log retention, alert routing, and review ownership.

## CIS Control 9: Email And Web Browser Protections

Cisco Umbrella integration, content filtering, and Layer 7 firewall controls can reduce malicious web access and phishing exposure.
Coverage depends on configuration, licensing, and whether user traffic traverses the protected path.

## CIS Control 10: Malware Defenses

MX Advanced Security features such as AMP and Threat Grid integration can provide malware inspection and protection.
Report checks should distinguish unavailable features from disabled or misconfigured features.

## CIS Control 11: Data Recovery

Meraki configuration history, cloud management, Auto VPN failover, and redundant topology design can support recovery.
They do not replace formal backups for endpoints, servers, SaaS platforms, or business data.

## CIS Control 12: Network Infrastructure Management

Meraki Dashboard centralizes infrastructure configuration and monitoring.
Network segmentation, least privilege, firmware planning, and change review should be evaluated together.

## CIS Control 13: Network Monitoring And Defense

IDS/IPS, Security Center, NetFlow, traffic analytics, event logs, and alerting can support monitoring and response.
Assessment should verify whether telemetry is enabled, reviewed, and sent to the right operational systems.

## CIS Control 14: Security Awareness And Skills Training

Meraki reports and alerting can provide operational evidence for IT staff training and user awareness.
This is supporting evidence only; formal awareness training usually lives outside Meraki.

## CIS Control 15: Service Provider Management

Meraki API integrations, monitoring, and Cisco trust resources can support service provider oversight.
Assessment should document third-party access, integration ownership, and vendor support dependencies.

## CIS Control 16: Application Software Security

Umbrella integration, content filtering, Layer 7 controls, and traffic analytics can reduce risky application exposure.
Application security itself usually requires additional controls outside the Meraki network layer.

## CIS Control 17: Incident Response Management

Security alerts, event logs, syslog export, and network telemetry support investigation and response.
Assessment should verify alert destinations, escalation paths, and incident response ownership.

## CIS Control 18: Penetration Testing

Meraki telemetry can help identify attack paths and validate segmentation, firewall, and IDS/IPS behavior during testing.
External penetration testing remains a separate activity.
