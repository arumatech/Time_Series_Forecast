using { ZRISK } from '../db/datamodel';

service CatalogService {

  @odata.draft.enabled
  entity FLATFILE as projection on ZRISK.FLATFILE;

  entity JOBSUMM as projection on ZRISK.JOBSUMM;

  @readonly
  @cds.search: { DCSID }
  entity DCSIDS as select from ZRISK.FLATFILE {
    key DCSID
  }
  group by DCSID;

  @readonly
  entity INSTRUMENTS as select from ZRISK.FLATFILE {
    key DCSID,
    key MIC
  }
  group by DCSID, MIC;

  action uploadCSV(
    csvText  : LargeString,
    fileName : String
  ) returns Integer;
}