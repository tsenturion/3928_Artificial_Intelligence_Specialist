"""Командная строка проекта.

Из папки проекта после установки пакета:
    python -m support_router run
    python -m support_router predict "Курьер не привёз мой заказ"

Команды prepare, train и evaluate также запускаются отдельно.
Для PowerShell предусмотрен файл Запустить_модель.ps1, не требующий установки пакета.

run выполняет весь цикл заново, а DVC решает, какие этапы действительно изменились.
verify проверяет уже подготовленную модель и её отчёт: эту команду вызывают перед
упаковкой и обновлением приложения. predict обслуживает одно обращение без сервера.
Параметр --config указывается перед подкомандой; в установленном контейнерном
пакете передавайте явный путь: python -m support_router --config /app/params.toml verify.
"""

import argparse
import json
from pathlib import Path

from support_router.config import DEFAULT_CONFIG, load_settings
from support_router.data import prepare
from support_router.evaluate import evaluate
from support_router.logging_utils import configure_logging
from support_router.model import load_model, predict, train
from support_router.release import verify_release


def main(argv: list[str] | None = None) -> int:
    """Выполняет выбранную операцию и возвращает код завершения для оболочки.

    argv=None означает чтение реальных аргументов процесса; список позволяет
    вызвать команду из проверки без изменения глобального sys.argv.
    Коды: 0 — успех, 1 — ошибка входов/окружения, 2 — модель не прошла порог.
    CI и PowerShell используют ненулевой код, чтобы остановить следующие шаги.
    """
    parser = argparse.ArgumentParser(description="Обучение и использование классификатора обращений")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG, help="Путь к params.toml")
    # Подкоманды задают один обязательный сценарий; условия ниже могут выполнять
    # несколько операций лишь для общей команды run.
    commands = parser.add_subparsers(dest="command", required=True)
    for name, description in (
        ("prepare", "Проверить и подготовить выборки"),
        ("train", "Обучить и сохранить Pipeline"),
        ("evaluate", "Проверить сохранённую модель на контроле"),
        ("run", "Последовательно подготовить данные, обучить и оценить"),
        ("verify", "Проверить модель и её отчёт перед выпуском"),
    ):
        commands.add_parser(name, help=description)
    prediction = commands.add_parser("predict", help="Классифицировать новое обращение")
    prediction.add_argument("text", help="Текст обращения в кавычках")
    # type=Path преобразует --config в путь, а argparse обрабатывает --help и
    # неправильные аргументы до попытки чтения данных или модели.
    args = parser.parse_args(argv)
    logger = configure_logging(args.config.resolve().parent / "logs")
    try:
        settings = load_settings(args.config)
        logger.info("Запуск команды %s.", args.command)
        if args.command in ("prepare", "run"):
            prepare(settings)
        if args.command in ("train", "run"):
            train(settings)
        if args.command in ("evaluate", "run"):
            metrics = evaluate(settings)
            print(f"Accuracy: {metrics['accuracy']:.3f}; macro-F1: {metrics['macro_f1']:.3f}")
            print(f"Отчёты: {settings.reports_dir}")
            # Отчёты уже сохранены и доступны для разбора неуспешного эксперимента.
            if not metrics["quality_gate"]["passed"]:
                logger.error("Модель не прошла установленную проверку качества.")
                return 2
        if args.command == "predict":
            result = predict(load_model(settings.model_path), args.text)
            # JSON в stdout удобен для будущей автоматизации; журналы идут в stderr и файл.
            print(json.dumps(result, ensure_ascii=False, indent=2))
        if args.command == "verify":
            result = verify_release(settings)
            print(f"Комплект готов: модель {result['model_version']}; macro-F1 {result['macro_f1']:.3f}")
        return 0
    except Exception:
        # Полный traceback остаётся в журнале и помогает отличить плохие данные
        # от отсутствующей модели или ошибки окружения. Тексты обращений не логируются.
        logger.exception("Команда %s завершилась с ошибкой.", args.command)
        return 1
