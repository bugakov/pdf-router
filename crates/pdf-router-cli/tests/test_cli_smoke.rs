use assert_cmd::Command;
use predicates::prelude::*;
use std::io::Write;
use tempfile::NamedTempFile;

#[test]
fn test_cli_help() {
    let mut cmd = Command::cargo_bin("pdf-router").unwrap();
    cmd.arg("--help")
        .assert()
        .success()
        .stdout(predicate::str::contains("Ultra-minimal, deterministic"));
}

#[test]
fn test_cli_unknown_file() {
    let mut cmd = Command::cargo_bin("pdf-router").unwrap();
    cmd.arg("non_existent_file.pdf").assert().failure().stderr(
        predicate::str::contains("No such file or directory")
            .or(predicate::str::contains("not found")),
    );
}

#[test]
fn test_cli_smoke_sample() {
    let mut tmp = NamedTempFile::new().unwrap();
    tmp.write_all(b"Hello world from router\n").unwrap();
    let path = tmp.path().to_str().unwrap();

    let mut cmd = Command::cargo_bin("pdf-router").unwrap();
    cmd.arg(path)
        .arg("--render")
        .arg("json")
        .assert()
        .success()
        .stdout(predicate::str::contains("\"ir_version\": \"0.6\""));
}

#[test]
fn test_cli_info_flag() {
    let mut tmp = NamedTempFile::new().unwrap();
    tmp.write_all(b"Hello info check\n").unwrap();
    let path = tmp.path().to_str().unwrap();

    let mut cmd = Command::cargo_bin("pdf-router").unwrap();
    cmd.arg(path).arg("--info").assert().success().stdout(
        predicate::str::contains("format:")
            .and(predicate::str::contains("page_count:"))
            .and(predicate::str::contains("pages_native:")),
    );
}

#[test]
fn test_cli_ocr_mock_flag() {
    let mut tmp = NamedTempFile::new().unwrap();
    tmp.write_all(b"Hello ocr flag\n").unwrap();
    let path = tmp.path().to_str().unwrap();

    let mut cmd = Command::cargo_bin("pdf-router").unwrap();
    cmd.arg(path)
        .arg("--ocr")
        .arg("mock")
        .arg("--strict")
        .assert()
        .success();
}
