# Affaldonline

Support for schedules from Affaldonline, which serves several Danish municipalities.

The layout of the response page may differ. There seems to be a default, but I've stumpled upon at least a few uniques.

## Configuration via configuration.yaml

```yaml
waste_collection_schedule:
    sources:
        - name: affaldonline_dk
          args:
              municipality: "favrskov"
              street: "Nørregade"
              house_number: "1"
              postal_code: "8382"
              city: "Hinnerup"
```

### Configuration Variables

**municipality**  
_(String) (required)_

An affaldonline defined string for the municipality. I.e. "aeroe" for "Ærø". 

A duckduckgo search revealed many of them:

- [DuckDuckGo](https://duckduckgo.com/?t=h_&q=site%3Aaffaldonline.dk%2Fkalender%2F&ia=web)

**street**

_(String) (required if `values` is not set)_

Street name.

**house_number**

_(String) (required if `values` is not set)_

House number as shown in the Affaldonline dropdown. Compound labels may be entered
with or without spaces, for example `14B`, `11 ST TV`, or `37Ai`.

**postal_code**

_(String) (optional, recommended)_

Postal code. Use this when a street name exists in multiple cities or postal
districts.

**city**

_(String) (optional, recommended)_

City or postal district. This is used to disambiguate the street when it matches
the Affaldonline lookup. If the postal code uniquely identifies the street but
Affaldonline uses a different district label, the source still uses the postal
code match.

**values**  
_(String) (optional, advanced)_

A string that includes the street name, house number, postal code, city name, and some numbers that seems to be internal affaldonline references.
If `values` is set, the address lookup is skipped.

The source resolves `street`, `house_number`, `postal_code`, and `city` to this
string automatically by calling Affaldonline's street and house-number lookup
endpoints. The resolved string is cached in memory for the running Home
Assistant process, so repeated fetches for the same address do not repeat the
lookup. The cache is process-local and is rebuilt after Home Assistant restarts.

Example with a compound house selector:

```yaml
waste_collection_schedule:
    sources:
        - name: affaldonline_dk
          args:
              municipality: "favrskov"
              street: "Examplevej"
              house_number: "11STTV"
              postal_code: "1234"
              city: "Eksempelby"
```

## How to find the "values" string to use

This is only needed for advanced troubleshooting or municipalities where the
automatic address lookup does not find the right house selector.

Go to the Affaldonline site for your municipality:

- [Ærø](https://www.affaldonline.dk/kalender/aeroe/)
- [Assens](https://www.affaldonline.dk/kalender/assens/)
- [Favrskov](https://www.affaldonline.dk/kalender/favrskov/)
- [Fanø](https://www.affaldonline.dk/kalender/fanoe/)
- [Faaborg-Midtfyn / FFV](https://www.ffv.dk/)
- [Fredericia](https://www.affaldonline.dk/kalender/fredericia/)
- [Holbæk / Fors A/S](https://www.fors.dk/affald/afhentning-af-affald/)
- [Langeland](https://www.affaldonline.dk/kalender/langeland/)
- [Middelfart](https://www.affaldonline.dk/kalender/middelfart/)
- [Nyborg](https://www.affaldonline.dk/kalender/nyborg/)
- [Rebild](https://www.affaldonline.dk/kalender/rebild/)
- [Silkeborg](https://www.affaldonline.dk/kalender/silkeborg/)
- [Sorø](https://www.affaldonline.dk/kalender/soroe/)
- [Vejle](https://www.affaldonline.dk/kalender/vejle/)

When the address page links a year calendar, the source reads that PDF and returns every collection date in it, including later days and weeks with pickups on more than one day. The following year is included when that calendar can be read as well. If the PDF cannot be read, the municipality's previous parser is used. Rebild and Vejle do not publish collection dates for the sample addresses in this file.

Open the developer console in your browser.

Enter your address and select the house number in the dropdown menu.

In the Network tab in the browser console, select the latest URL ending in **showInfo.php** and look under Payload, where you should find the **values** variable ready for copy/paste.

Depending on your browser, it should also be possible to right-click the house number field, and select "Inspect" - this should give you the html for the field, and here you can select the value from your house number in the list.

### Customizing Waste Types

Customizing waste types is a feature of the Waste Collection Schedule component and is very useful here, since the waste types in affaldonline are often long and not very consistent.

```yaml
waste_collection_schedule:
    sources:
        - name: affaldonline_dk
          args:
              municipality: "favrskov"
              values: "Nørregade|1||||8382|Hinnerup|6443|108156|0"
          customize:
              - type: "Plast/mad- og drikkekartoner og glas/metal"
                alias: "Plast/glas/metal"
```
