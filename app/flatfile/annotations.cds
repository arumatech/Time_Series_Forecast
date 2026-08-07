using CatalogService as service from '../../srv/cat-service';
annotate service.FLATFILE with @(
    UI.FieldGroup #GeneratedGroup : {
        $Type : 'UI.FieldGroupType',
        Data : [
            {
                $Type : 'UI.DataField',
                Value : DCSID,
            },
            {
                $Type : 'UI.DataField',
                Value : MIC,
            },
            {
                $Type : 'UI.DataField',
                Value : PRICETYPE,
            },
            {
                $Type : 'UI.DataField',
                Value : MKEYDT,
            },
            {
                $Type : 'UI.DataField',
                Value : PRICEDATE,
            },
            {
                $Type : 'UI.DataField',
                Value : PRICE,
            },
            {
                $Type : 'UI.DataField',
                Value : PER,
            },
            {
                $Type : 'UI.DataField',
                Value : UOM,
            },
            {
                $Type : 'UI.DataField',
                Value : CURRENCY,
            },
            {
                $Type : 'UI.DataField',
                Value : FILENAME,
            },
        ],
    },
    UI.Facets : [
        {
            $Type : 'UI.ReferenceFacet',
            ID : 'GeneratedFacet1',
            Label : 'General Information',
            Target : '@UI.FieldGroup#GeneratedGroup',
        },
    ],
    UI.LineItem : [
        {
            $Type : 'UI.DataField',
            Value : PRICEDATE,
        },
        {
            $Type : 'UI.DataField',
            Value : MKEYDT,
        },
        {
            $Type : 'UI.DataField',
            Value : PRICETYPE,
        },
        {
            $Type : 'UI.DataField',
            Value : MIC,
        },
        {
            $Type : 'UI.DataField',
            Value : DCSID,
        },
    ],
);

