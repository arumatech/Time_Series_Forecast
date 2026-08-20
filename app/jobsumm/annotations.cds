using CatalogService as service from '../../srv/cat-service';

annotate service.JOBSUMM with @(
    UI.FieldGroup #GeneratedGroup : {
        $Type : 'UI.FieldGroupType',
        Data : [
            {
                $Type : 'UI.DataField',
                Label : 'Job ID',
                Value : JOB_ID,
            },
            {
                $Type : 'UI.DataField',
                Label : 'Job Type',
                Value : FORCORR,
            },
            {
                $Type : 'UI.DataField',
                Label : 'Model',
                Value : MODEL,
            },
            {
                $Type : 'UI.DataField',
                Label : 'Source DCSID',
                Value : SRCDCSID,
            },
            {
                $Type : 'UI.DataField',
                Label : 'Source MIC',
                Value : SRCMIC,
            },
            {
                $Type : 'UI.DataField',
                Label : 'Target DCSID',
                Value : TARDCSID,
            },
            {
                $Type : 'UI.DataField',
                Label : 'Target MIC',
                Value : TARMIC,
            },
            {
                $Type : 'UI.DataField',
                Label : 'Horizon Type',
                Value : HORTY,
            },
            {
                $Type : 'UI.DataField',
                Label : 'Horizon',
                Value : HORVAL,
            },
            {
                $Type : 'UI.DataField',
                Label : 'Job Start',
                Value : JOBSTTMSTMP,
            },
            {
                $Type : 'UI.DataField',
                Label : 'Job End',
                Value : JOBENDTMSTMP,
            },
            {
                $Type : 'UI.DataField',
                Label : 'Completion %',
                Value : JOBCOMPPCT,
            },
            {
                $Type : 'UI.DataField',
                Label : 'Created At',
                Value : createdAt,
            },
            {
                $Type : 'UI.DataField',
                Label : 'Created By',
                Value : createdBy,
            },
            {
                $Type : 'UI.DataField',
                Label : 'Modified At',
                Value : modifiedAt,
            },
            {
                $Type : 'UI.DataField',
                Label : 'Modified By',
                Value : modifiedBy,
            },
        ],
    },

    UI.Facets : [
        {
            $Type : 'UI.ReferenceFacet',
            ID : 'GeneralInformation',
            Label : 'General Information',
            Target : '@UI.FieldGroup#GeneratedGroup',
        },
    ],

    UI.LineItem : [
        {
            $Type : 'UI.DataField',
            Label : 'Job ID',
            Value : JOB_ID,
        },
        {
            $Type : 'UI.DataField',
            Label : 'Job Type',
            Value : FORCORR,
        },
        {
            $Type : 'UI.DataField',
            Label : 'Model',
            Value : MODEL,
        },
        {
            $Type : 'UI.DataField',
            Label : 'Source DCSID',
            Value : SRCDCSID,
        },
        {
            $Type : 'UI.DataField',
            Label : 'Source MIC',
            Value : SRCMIC,
        },
        {
            $Type : 'UI.DataField',
            Label : 'Target DCSID',
            Value : TARDCSID,
        },
        {
            $Type : 'UI.DataField',
            Label : 'Target MIC',
            Value : TARMIC,
        },
        {
            $Type : 'UI.DataField',
            Label : 'Horizon Type',
            Value : HORTY,
        },
        {
            $Type : 'UI.DataField',
            Label : 'Horizon',
            Value : HORVAL,
        },
        {
            $Type : 'UI.DataField',
            Label : 'Job Start',
            Value : JOBSTTMSTMP,
        },
        {
            $Type : 'UI.DataField',
            Label : 'Job End',
            Value : JOBENDTMSTMP,
        },
        {
            $Type : 'UI.DataField',
            Label : 'Completion %',
            Value : JOBCOMPPCT,
        },
    ],
);