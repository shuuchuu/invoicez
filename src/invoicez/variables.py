from collections.abc import Mapping
from datetime import date, timedelta
from functools import cached_property
from typing import Any, Literal, Self

from deepmerge import always_merger
from pydantic import BaseModel, computed_field, model_validator


class TVA(BaseModel, extra="forbid", frozen=True):
    category: str
    rate: float


def compute_vat_id(siren: str) -> str:
    vat_key = (12 + 3 * (int(siren) % 97)) % 97
    return f"FR{vat_key:02}{siren}"


class Seller(BaseModel, extra="forbid", frozen=True):
    name: str
    address: str
    city: str
    postcode: str
    country: str
    email: str
    phone: str
    iban: str
    bic: str
    account_name: str
    siren: str
    siret: str
    electronic_address: str
    electronic_address_scheme: str
    means_code: str = "30"

    @computed_field
    @cached_property
    def vat_id(self) -> str:
        return compute_vat_id(self.siren)

    @model_validator(mode="after")
    def _check_siret_matches_siren(self) -> Self:
        # BR-FR-09: the SIRET's first 9 digits must match the SIREN, and the
        # SIRET must be 14 digits long.
        if len(self.siret) != 14:
            msg = f"siret must be 14 digits (BR-FR-09), got {self.siret!r}"
            raise ValueError(msg)
        if not self.siret.startswith(self.siren):
            msg = (
                f"siret {self.siret!r} must start with siren {self.siren!r} (BR-FR-09)"
            )
            raise ValueError(msg)
        return self


class Company(BaseModel, extra="forbid", frozen=True):
    name: str
    address: str
    city: str
    postcode: str
    country: str
    siren: str
    maximum_days: int
    electronic_address: str
    electronic_address_scheme: str
    # Whether the client wants to be invoiced against a contract (BT-12) or a
    # purchase order (BT-13); this varies per client.
    reference_kind: Literal["contract", "order"] = "order"


class InvoiceLine(BaseModel, extra="forbid", frozen=True):
    start: date | None = None
    end: date | None = None
    pu: float
    n: float
    description: str
    unit: str


class Notices(BaseModel, extra="forbid", frozen=True):
    recouvrement: str  # Indemnité recouvrement
    taux_retard: str  # Taux retard de paiement
    escompte: str  # Escompte (obligatoire)
    legal: str  # Informations légales (NDA)
    general: str  # Autres informations


class InvoiceMeta(BaseModel, extra="forbid", frozen=True):
    type_invoice: str
    spec_format: str
    currency: str
    type_code: str = "380"  # 380 Facture, 381 Avoir


class Variables(BaseModel, extra="forbid", frozen=True):
    document_number: str
    date: date
    description: str | None = None
    quote: bool = False
    previous_document_number: str | None = None
    previous_document_date: date | None = None
    # Client's contract or purchase order reference, mapped to BT-12 or
    # BT-13 depending on company.reference_kind.
    reference: str | None = None
    tva: TVA
    user: Seller
    company: Company
    products: list[InvoiceLine]
    invoice_meta: InvoiceMeta
    notices: Notices
    latex_extras: dict[str, Any]

    @computed_field
    @cached_property
    def due_date(self) -> date:
        return self.date + timedelta(days=self.company.maximum_days)

    @classmethod
    def from_mappings(
        cls, global_mapping: Mapping[str, Any], invoice_mapping: Mapping[str, Any]
    ) -> Self:
        data = {k: v for k, v in global_mapping.items() if k != "companies"}
        always_merger.merge(data, invoice_mapping)
        data["company"] = {
            **global_mapping["company"],
            **global_mapping["companies"][invoice_mapping["company"]],
        }
        return cls.model_validate(data)
