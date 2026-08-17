# Untitled

Created by: Evan Kersten
Created time: August 17, 2026 8:04 AM
Last edited by: Evan Kersten
Last updated time: August 17, 2026 8:04 AM

### Data Inventory

| **Data Element** | **Count** |
| --- | --- |
| **Total Item Codes** | 312 |
| **Activity Types** | 5 (Revenue, Expenditure, Debt, Cash & Securities, Employment) |
| **Service Areas** | 12 (Transportation, Education, Public Safety, Health, etc.) |
| **Government Functions** | 38 (distinct functional areas) |
| **Revenue Item Codes** | 105 |
| **Expenditure Item Codes** | 156 |
| **Debt Item Codes** | 10 |

**Expenditures only: Service Areas**

| Service Area | Functions | Function Codes | Unique Item Codes |
| --- | --- | --- | --- |
| **Transportation** | 5 | 01, 44, 45, 60, 87 | 20 |
| **Public Safety** | 5 | 04, 05, 24, 62, 66 | 20 |
| **Education** | 6 | 12, 16, 18, 19, 20, 21 | 22 |
| **Health & Human Services** | 11 | 22, 32, 36, 37, 39, 67, 68, 74, 75, 77, 79 | 34 |
| **Environment & Natural Resources** | 6 | 55, 56, 57, 58, 59, 81 | 24 |
| **Culture & Recreation** | 2 | 52, 61 | 8 |
| **Utilities** | 5 | 80, 91, 92, 93, 94 | 25 |
| **Housing & Community Development** | 1 | 50 | 4 |
| **General Government** | 2 | 29, 31 | 8 |
| **Financial Administration** | 1 | 23 | 4 |
| **Judicial** | 1 | 25 | 4 |
| **Other** | 3 | 03, 89, 90 | 13 |
| **TOTAL** | **48** |  | **186** |

**Functions Mapped to Service Areas**

| Service Area | Function Code (item_code.code) | Function Name (new) |
| --- | --- | --- |
| Transportation | **01** | Air Transportation |
| Other | **03** | Miscellaneous Commercial Activities |
| Public Safety | **04** | Correction - Institutions |
| Public Safety | **05** | Correction - Other |
| Education | **12** | Elementary & Secondary Education |
| Education | **16** | Higher Education - Auxiliary |
| Education | **18** | Higher Education - Instructional |
| Education | **19** | Education - Exhibit/Library (State) |
| Education | **20** | Education - Research (State) |
| Education | **21** | Education - Other (State) |
| Health & Human Services | **22** | Employment Security Administration |
| Financial Administration | **23** | Financial Administration |
| Public Safety | **24** | Fire Protection |
| Judicial | **25** | Judicial & Legal |
| General Government | **29** | Central Staff / General Administration |
| General Government | **31** | General Public Buildings |
| Health & Human Services | **32** | Health |
| Health & Human Services | **36** | Hospitals |
| Health & Human Services | **37** | Hospitals - Other |
| Health & Human Services | **39** | Hospitals - NEC |
| Transportation | **44** | Regular Highways |
| Transportation | **45** | Toll Highways |
| Housing & Community Development | **50** | Housing & Community Development |
| Culture & Recreation | **52** | Libraries |
| Environment & Natural Resources | **55** | Natural Resources - Conservation |
| Environment & Natural Resources | **56** | Natural Resources - Development |
| Environment & Natural Resources | **57** | Natural Resources - Recreation |
| Environment & Natural Resources | **58** | Natural Resources - Forestry |
| Environment & Natural Resources | **59** | Natural Resources - Other |
| Transportation | **60** | Parking Facilities |
| Culture & Recreation | **61** | Parks & Recreation |
| Public Safety | **62** | Police Protection |
| Public Safety | **66** | Protective Inspection |
| Health & Human Services | **67** | Public Welfare - Cash Assistance |
| Health & Human Services | **68** | Public Welfare - Vendor Payments (Non-Medical) |
| Health & Human Services | **74** | Public Welfare - Vendor Payments (Medical) |
| Health & Human Services | **75** | Public Welfare - Vendor Payments NEC |
| Health & Human Services | **77** | Public Welfare - Institutions |
| Health & Human Services | **79** | Public Welfare - Other |
| Utilities | **80** | Sewerage |
| Environment & Natural Resources | **81** | Solid Waste Management |
| Transportation | **87** | Sea and Inland Port Facilities |
| Other | **89** | All Other & Unallocable |
| Other | **90** | State Liquor Stores |
| Utilities | **91** | Water Supply |
| Utilities | **92** | Electric Power |
| Utilities | **93** | Gas Supply |
| Transportation | **94** | Transit |

**Employee Types Mapped to Service Areas and Functions**

| Service Area | Function Code (our new code) | Function Name (actual function name) | Employee Function (actual) |
| --- | --- | --- | --- |
| Transportation | 01 | Air Transportation | Air Transportation |
| Other | 89 | All Other & Unallocable | All other and unallocable |
| Public Safety | 04, 05 | Correction - Institutions; Correction - Other | Corrections |
| Education | 12 | Elementary & Secondary Education | Education - Elementary and Secondary Instructional |
| Education | 18 | Higher Education - Instructional | Education - Higher Education Instructional |
| Education | 16, 18 | Higher Education - Auxiliary; Higher Education - Instructional | Education - Higher Education Other |
| Education | 19, 20, 21 | Education - Exhibit/Library (State); Education - Research (State); Education - Other (State) | Education - Other |
| Utilities | 92 | Electric Power | Electric Power |
| Financial Administration | 23 | Financial Administration | Financial Administration |
| Public Safety | 24 | Fire Protection | Fire Protection - Firefighters |
| Public Safety | 24 | Fire Protection | Fire Protection - Other |
| Utilities | 93 | Gas Supply | Gas Supply |
| Health & Human Services | 32 | Health | Health |
| Transportation | 44, 45 | Regular Highways; Toll Highways | Highways |
| Health & Human Services | 36, 37, 39 | Hospitals; Hospitals - Other; Hospitals - NEC | Hospitals |
| Housing & Community Development | 50 | Housing & Community Development | Housing and Community Development |
| Judicial | 25 | Judicial & Legal | Judicial and Legal |
| Culture & Recreation | 52 | Libraries | Libraries |
| Environment & Natural Resources | 55, 56, 57, 58, 59 | Natural Resources - Conservation; Natural Resources - Development; Natural Resources - Recreation; Natural Resources - Forestry; Natural Resources - Other | Natural Resources |
| General Government | 29 | Central Staff / General Administration | Other Government Administration |
| Culture & Recreation | 61 | Parks & Recreation | Parks and Recreation |
| Public Safety | 62 | Police Protection | Police Protection - Persons with Power of Arrest |
| Public Safety | 62 | Police Protection | Police Protection - Other |
| Health & Human Services | 67, 68, 74, 75, 77, 79 | Public Welfare - Cash Assistance; Public Welfare - Vendor Payments (Non-Medical); Public Welfare - Vendor Payments (Medical); Public Welfare - Vendor Payments NEC; Public Welfare - Institutions; Public Welfare - Other | Public Welfare |
| Transportation | 87 | Sea and Inland Port Facilities | Sea and Inland Port Facilities |
| Utilities | 80 | Sewerage | Sewerage |
| Health & Human Services | 22 | Employment Security Administration | Social Insurance Administration |
| Environment & Natural Resources | 81 | Solid Waste Management | Solid Waste Management |
| Other | 90 | State Liquor Stores | State liquor stores |
| Transportation | 94 | Transit | Transit |
| Utilities | 91 | Water Supply | Water Supply |

**Expenditure Types**

| **Code** | **Expenditure Type** | **Description** |
| --- | --- | --- |
| **E** | Current Operations | Salaries, supplies, services, utilities, maintenance |
| **F** | Construction | New construction, infrastructure projects |
| **G** | Capital Outlay | Equipment, land acquisition, improvements |
| **K** | Capital Not Classified | Capital spending not elsewhere classified |
| **I** | Interest on Debt | Interest payments on outstanding debt |
| **J** | Assistance & Subsidies | Direct payments to individuals, scholarships |