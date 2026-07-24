from homefit_api.demo import evaluate_demo_dataset, load_demo_dataset


def test_three_demo_scenarios_are_offline_and_reproducible() -> None:
    report = evaluate_demo_dataset()

    assert report.dataset_version == "demo-scenarios-v1"
    assert len(report.scenarios) == 3
    assert report.all_reproducible is True
    assert all(not item.external_services_required for item in report.scenarios)
    assert all(item.document_safe for item in report.scenarios)
    assert all(len(item.extracted_fields) >= 6 for item in report.scenarios)


def test_demo_results_are_deterministic() -> None:
    assert evaluate_demo_dataset() == evaluate_demo_dataset()


def test_changed_expected_winner_fails_demo_gate() -> None:
    dataset = load_demo_dataset().model_copy(deep=True)
    dataset.scenarios[0].expected_winner = "not-the-winner"

    report = evaluate_demo_dataset(dataset)

    assert report.all_reproducible is False
    assert report.scenarios[0].reproducible is False
