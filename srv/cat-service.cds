using { ZRISK } from '../db/datamodel';
using { TBAC_DCS_MIC_SOURCE } from '../db/tbac';

service CatalogService {

  @odata.draft.enabled
  @restrict: [
    {
      grant: 'READ',
      to: 'FlatFileRead'
    },
    {
      grant: [
        'CREATE',
        'UPDATE',
        'DELETE'
      ],
      to: 'FlatFileManage'
    }
  ]
  entity FLATFILE as projection on ZRISK.FLATFILE;

  @restrict: [
    {
      grant: 'READ',
      to: 'JobRead'
    },
    {
      grant: 'CREATE',
      to: 'JobCreate'
    }
  ]
  entity JOBSUMM as projection on ZRISK.JOBSUMM;

    @readonly
  @restrict: [
    {
      grant: 'READ',
      to: 'JobRead'
    }
  ]
  entity ForecastCorrelations as projection on ZRISK.FORCORR;

  @readonly
  @restrict: [
    {
      grant: 'READ',
      to: [
        'FlatFileRead',
        'JobRead'
      ]
    }
  ]
  entity INSTRUMENTS as
    select from ZRISK.FLATFILE {
      key DCSID,
      key MIC
    }
    union
    select from TBAC_DCS_MIC_SOURCE {
      DCSID,
      MIC
    };

  @readonly
  @cds.search: { DCSID }
  @restrict: [
    {
      grant: 'READ',
      to: [
        'FlatFileRead',
        'JobRead'
      ]
    }
  ]
  entity DCSIDS as
    select from INSTRUMENTS {
      key DCSID
    }
    group by DCSID;

  @requires: 'FlatFileManage'
  action uploadCSV(
    csvText  : LargeString,
    fileName : String
  ) returns Integer;
}