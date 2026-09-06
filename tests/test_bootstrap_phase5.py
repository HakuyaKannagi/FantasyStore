from fantasy_store.bootstrap import bootstrap_phase5


def test_bootstrap_phase5_preserves_order_and_exposes_application_services(tmp_path):
    rt=bootstrap_phase5(tmp_path/"data")
    try:
        assert rt.phase4.pack_recovery is not None
        assert rt.catalog_service.get_products().catalog_state=="NO_PACKS"
        assert rt.cart_service.get_cart().lines==()
        assert rt.history_service.get_order_history().total==0
        assert rt.stats_service.get_statistics().order_count==0
        assert rt.pack_service.get_packs().pack_state=="NO_PACKS"
    finally:
        rt.close()
