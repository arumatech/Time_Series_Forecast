using { ZRISK } from '../db/datamodel';
using { TBAC_DCS_MIC_SOURCE } from '../db/tbac';

service CatalogService {

  @odata.draft.enabled
  entity FLATFILE as projection on ZRISK.FLATFILE;

  entity JOBSUMM as projection on ZRISK.JOBSUMM;

  @readonly
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
  entity DCSIDS as select from INSTRUMENTS {
    key DCSID
  }
  group by DCSID;

  action uploadCSV(
    csvText  : LargeString,
    fileName : String
  ) returns Integer;
}