# Contract sources and limits

The user supplied `IMKONEX_4TOCHKI_REPORT.json` from successful SOAP diagnostics in September 2026. Only field names and types, not credentials or purchase values, were used to extend the application.

Confirmed fields: GetRest(filter.wrh, filter.page) → restItems.restItem(code,rest); GetGoodsInfo(code_list) → pressureSensorList.PressureSensorContainer, oilList.OilContainer, fastenerList.FastenerContainer, sparePartList.SparePartContainer, cameraList.CameraContainer; GetGoodsPriceRestByCode(filter.code_list, searchCodeByOccurence, include_paid_delivery) → price_rest_list.price_rest(code,whpr.wh_price_rest(price,price_rozn,rest,wrh)).

GetRest pagination origin is detected from pages 0 and 1. An empty page terminates a scan. Repeated/overlapping pages, malformed data, network failures and exhausted budgets abort atomic publication. This extended traversal has not yet been verified with a live supplier account. A provider error on a later terminal page is deliberately not guessed to mean an empty page.

RUB currency confirmation is inherited from the successful GetFindTyre/GetFindDisk currencyRate checks in the same import; the price-by-code response has no independent currency field in the supplied schema.

No unverified GetFindOil/GetFindSensor methods, guessed type IDs, or inferred third supplier price are used. `price_rozn` is explicitly labeled as supplier retail, not claimed to be an independently supplied manufacturer RRP. New accessories are published only with a retail price and stock. There is no fake seed inventory for these categories.

Main navigation is extracted from https://www.imkonex.com/ IDs `imxGlobalHeader` and `imxPremiumFooter`; arbitrary remote scripts and handlers are stripped. Shared CSS and four brand SVG files are bundled from the current main site / existing owned automobile catalog. Published menu content refreshes every 300 seconds. A future redesign that changes those IDs requires adapting the extractor; until then the last good content is kept. Last refresh is available in the authenticated manager status response.

Public product details use GetGoodsInfo and an explicit technical whitelist: noise, grip, comfort, aquaplaning, passability, softness, wear_index, initial_tread_depth, protector_type, axle, load_index, speed_index, puncture, tonnage, thorn and dimensional fields. Noise units/scales are not inferred. Empty fields and unknown RunFlat/XL remain unknown. The public details endpoint exposes only product ID and safe label/value characteristics; provider price objects never pass through it. Responses are cached for 24 hours.
