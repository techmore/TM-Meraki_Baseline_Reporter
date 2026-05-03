# Meraki Recommendations: Contoso Corporation

## Scope
- Networks: 3
- Devices: 50
- Switches: 12
- Wireless APs: 30
- Cameras: 6
- Offline devices: 0

## Executive Summary
- Network utilization is at 65% average with peaks reaching 85% during business hours
- PoE power consumption is within normal parameters but approaching capacity limits on two switches
- All devices are reporting online status

## Inventory Summary
- appliance: 2
- switch: 12
- wireless: 30
- camera: 6

### Top Models
- MR46: 24
- MS225-48FP: 8
- MX250: 2
- MS225-24P: 4
- MV72: 6

## Switch Chain And Port Health
- No switch port errors or warnings detected
- All uplink connections are operating at 1Gbps or higher
- No duplex mismatches detected

## Availability
- All devices reported online in the API snapshot.
- 99.98% uptime over the past 30 days

## Capacity And Bottlenecks
- Network utilization averages 65% with peaks of 85% during 9AM-5PM
- Consider network segmentation to isolate broadcast domains
- Two access points showing >75% utilization may benefit from channel adjustment

## Purchasing And Upgrade Targets
- Current infrastructure supports growth for approximately 18-24 months
- Consider upgrading core switches to 10Gbps capabilities for future growth
- Wireless controller upgrade recommended for enhanced management features

## Wireless AP Placement And Power
- Wireless coverage shows minimal overlap in most areas
- Two conference rooms show elevated RF noise levels
- All APs operating within recommended power levels

### RF Profiles (Summary)
- Network Main Office: 2.4GHz channel 1, 5GHz channel 36
- Network Conference Rooms: 2.4GHz channel 6, 5GHz channel 40
- Network Guest Network: 2.4GHz channel 11, 5GHz channel 149

## Cleanup And Removals
- No dormant devices detected
- All switch ports properly documented

## PoE Power
- PoE utilization at 68% of total capacity
- Review PoE-heavy switches for budget headroom

### Top Switches By PoE Energy (Last 24h)
- Switch Q2AA-BBBB-CCCC: 3450.2 Wh (avg 143.8 W)
- Switch Q2BB-BBBB-CCCC: 2890.5 Wh (avg 120.4 W)
- Switch Q2CC-BBBB-CCCC: 1875.3 Wh (avg 78.1 W)

### Top Ports By PoE Energy (Last 24h)
- Switch Q2AA-BBBB-CCCC Port 1: 245.6 Wh (neighbor MV72 - Parking Lot Camera 1)
- Switch Q2AA-BBBB-CCCC Port 2: 238.9 Wh (neighbor MV72 - Parking Lot Camera 2)
- Switch Q2BB-BBBB-CCCC Port 24: 210.5 Wh (neighbor MR46 - Conference Room AP)

## Next Actions
1. Monitor network utilization and consider QoS implementation for critical applications
2. Schedule quarterly physical inspection of all network equipment
3. Review and update network documentation including diagrams and IP schemes
4. Consider implementing network segmentation for improved security and performance
5. Plan for future capacity upgrades based on growth projections