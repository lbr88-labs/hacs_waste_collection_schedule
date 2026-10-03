# Affaldonline

Support for schedules from [Affaldonline](https://affaldonline.dk), which serves several Danish municipalities, including Fors in Holbæk.

The address page normally shows only the next emptying. When that page links a year calendar, every pickup date in the calendar is imported. If the calendar cannot be read, the next emptying printed on the page is used instead.

## Configuration via configuration.yaml

```yaml
waste_collection_schedule:
  sources:
    - name: affaldonline_dk
      args:
        municipality: holbaek
        street: STREET
        house_number: HOUSE_NUMBER
        postal_code: POSTAL_CODE
        city: CITY
```

### Configuration Variables

**municipality**
*(string) (required)*

Affaldonline municipality key, for example `holbaek`.

**values**
*(string) (optional)*

Advanced Affaldonline values string from the house-number field. When set, street lookup is skipped. Provide either `values` or `street` plus `house_number`.

**street**
*(string) (optional)*

Street name. Required when `values` is not set.

**house_number**
*(string) (optional)*

House number, including letter, floor or door when the calendar shows them. Required when `values` is not set.

**postal_code**
*(string) (optional)*

Postal code. Use it when the same street name exists in more than one town.

**city**
*(string) (optional)*

City or postal district. A postal-code match is still used when Affaldonline labels the district differently.

## How to get the source arguments

Open the Affaldonline calendar for the municipality and search for the address. Copy the values string from the selected house number, or enter the street and house number and let the source resolve it.
