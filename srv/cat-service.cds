using {ZRISK, } from '../db/datamodel';

service CatalogService {

  @odata.draft.enabled
  entity FLATFILE               as projection on ZRISK.FLATFILE;
}