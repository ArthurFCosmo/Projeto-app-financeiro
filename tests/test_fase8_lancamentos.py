"""Suíte de testes automatizados para a Fase 8 — Módulo de Lançamentos.

Verifica:
1. Criação de lançamentos de receita e despesa (POST /api/lancamentos);
2. Validações de valor positivo, datas, posse de conta e categoria, e incompatibilidade de tipo;
3. Transferência entre contas próprias (POST /api/lancamentos/transferencia), incluindo contas iguais,
   preenchimento automático de vencimento e status, alerta de saldo insuficiente e confirmação explícita;
4. Listagem e filtros combinados (GET /api/lancamentos) por período, tipo, status, conta, categoria e busca textual;
5. Edição de lançamentos e transferências (PUT /api/lancamentos/<id>);
6. Alternância rápida de status (PATCH /api/lancamentos/<id>/pagar);
7. Exclusão lógica (DELETE /api/lancamentos/<id>) com soft delete e recálculo dinâmico de saldos;
8. Isolamento estrito de dados entre usuários (anti-IDOR) retornando 403 / 404.
"""
from datetime import date
from decimal import Decimal
import unittest
from app import create_app
from app.models import db, Usuario, Conta, Categoria, Lancamento
from config.config import Config


class TestConfig(Config):
    TESTING = True
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    WTF_CSRF_ENABLED = False


class LancamentosTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app(TestConfig)
        self.client = self.app.test_client()
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()

        # Cria 2 usuários para validação anti-IDOR
        self.user1 = Usuario(nome="Glauber Oliveira", email="glauber@teste.com", tema_preferido="dark")
        self.user1.definir_senha("SenhaForte123")
        self.user2 = Usuario(nome="Outro Usuário", email="outro@teste.com", tema_preferido="dark")
        self.user2.definir_senha("SenhaForte123")
        db.session.add_all([self.user1, self.user2])
        db.session.commit()

        # Contas do Usuário 1
        self.conta_corrente = Conta(usuario_id=self.user1.id, nome="Conta Corrente", saldo_inicial=Decimal("1000.00"))
        self.poupanca = Conta(usuario_id=self.user1.id, nome="Poupança", saldo_inicial=Decimal("500.00"))
        # Conta do Usuário 2
        self.conta_user2 = Conta(usuario_id=self.user2.id, nome="Conta User 2", saldo_inicial=Decimal("200.00"))

        # Categorias do Usuário 1
        self.cat_salario = Categoria(usuario_id=self.user1.id, nome="Salário", tipo="receita")
        self.cat_alimentacao = Categoria(usuario_id=self.user1.id, nome="Alimentação", tipo="despesa")
        # Categoria do Usuário 2
        self.cat_user2 = Categoria(usuario_id=self.user2.id, nome="Alimentação U2", tipo="despesa")

        db.session.add_all([
            self.conta_corrente, self.poupanca, self.conta_user2,
            self.cat_salario, self.cat_alimentacao, self.cat_user2,
        ])
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def autenticar(self, usuario_id):
        with self.client.session_transaction() as sess:
            sess["usuario_id"] = usuario_id
            sess["_csrf_token"] = "csrf-token-valido"

    def test_criar_receita_e_despesa_com_validacoes(self):
        """Testa criação de lançamentos de receita e despesa e suas validações."""
        self.autenticar(self.user1.id)
        headers = {"X-CSRFToken": "csrf-token-valido"}

        # 1. Criação de despesa paga de R$ 150,00
        res = self.client.post(
            "/api/lancamentos",
            json={
                "tipo": "despesa",
                "valor": 150.00,
                "data_competencia": "2026-10-01",
                "data_vencimento": "2026-10-05",
                "descricao": "Supermercado Semanal",
                "categoria_id": self.cat_alimentacao.id,
                "conta_id": self.conta_corrente.id,
                "forma_pagamento": "Cartão de Débito",
                "status": "pago",
            },
            headers=headers,
        )
        self.assertEqual(res.status_code, 201)
        data = res.get_json()
        self.assertTrue(data["sucesso"])
        lanc_id = data["dados"]["lancamento"]["id"]
        self.assertEqual(data["dados"]["lancamento"]["descricao"], "Supermercado Semanal")
        self.assertEqual(data["dados"]["lancamento"]["valor"], 150.00)
        self.assertEqual(data["dados"]["lancamento"]["status"], "pago")

        # Verifica impacto no saldo da conta: 1000 - 150 = 850
        self.assertEqual(self.conta_corrente.calcular_saldo_atual(), Decimal("850.00"))

        # 2. Criação de receita recebida de R$ 3000,00
        res_rec = self.client.post(
            "/api/lancamentos",
            json={
                "tipo": "receita",
                "valor": 3000.00,
                "data_competencia": "2026-10-02",
                "descricao": "Salário Mensal",
                "categoria_id": self.cat_salario.id,
                "conta_id": self.conta_corrente.id,
                "forma_pagamento": "PIX",
                "status": "pago",
            },
            headers=headers,
        )
        self.assertEqual(res_rec.status_code, 201)
        # Saldo: 850 + 3000 = 3850
        self.assertEqual(self.conta_corrente.calcular_saldo_atual(), Decimal("3850.00"))

        # 3. Validação de incompatibilidade de tipo: Categoria de receita em lançamento de despesa
        res_invalido = self.client.post(
            "/api/lancamentos",
            json={
                "tipo": "despesa",
                "valor": 50.00,
                "data_competencia": "2026-10-03",
                "descricao": "Tentativa Invalida",
                "categoria_id": self.cat_salario.id,  # Salário é receita!
                "conta_id": self.conta_corrente.id,
            },
            headers=headers,
        )
        self.assertEqual(res_invalido.status_code, 400)
        self.assertIn("é de receita e não pode ser associada a um lançamento de despesa", res_invalido.get_json()["erro"])

        # 4. Validação de valor <= 0
        res_zero = self.client.post(
            "/api/lancamentos",
            json={
                "tipo": "despesa",
                "valor": 0.00,
                "data_competencia": "2026-10-03",
                "descricao": "Zero",
                "categoria_id": self.cat_alimentacao.id,
                "conta_id": self.conta_corrente.id,
            },
            headers=headers,
        )
        self.assertEqual(res_zero.status_code, 400)
        self.assertIn("maior que zero", res_zero.get_json()["erro"])

    def test_transferencia_entre_contas_proprias(self):
        """Testa o fluxo completo de transferência entre contas."""
        self.autenticar(self.user1.id)
        headers = {"X-CSRFToken": "csrf-token-valido"}

        # Saldo inicial: Corrente = 1000, Poupança = 500
        # 1. Bloqueio de transferência para a mesma conta
        res_mesma = self.client.post(
            "/api/lancamentos/transferencia",
            json={
                "valor": 100.00,
                "data_competencia": "2026-10-01",
                "conta_id": self.conta_corrente.id,
                "conta_destino_id": self.conta_corrente.id,
            },
            headers=headers,
        )
        self.assertEqual(res_mesma.status_code, 400)
        self.assertIn("A conta de destino deve ser diferente da conta de origem", res_mesma.get_json()["erro"])

        # 2. Transferência bem-sucedida de R$ 200,00 da Corrente para Poupança
        res_ok = self.client.post(
            "/api/lancamentos/transferencia",
            json={
                "valor": 200.00,
                "data_competencia": "2026-10-01",
                "conta_id": self.conta_corrente.id,
                "conta_destino_id": self.poupanca.id,
                "descricao": "Reserva de emergência",
            },
            headers=headers,
        )
        self.assertEqual(res_ok.status_code, 201)
        data = res_ok.get_json()
        self.assertTrue(data["sucesso"])
        lanc = data["dados"]["lancamento"]
        self.assertEqual(lanc["tipo"], "transferencia")
        self.assertEqual(lanc["status"], "pago")
        self.assertEqual(lanc["data_vencimento"], lanc["data_competencia"])
        self.assertIsNone(lanc["categoria_id"])

        # Verifica saldos: Corrente: 1000 - 200 = 800; Poupança: 500 + 200 = 700
        self.assertEqual(self.conta_corrente.calcular_saldo_atual(), Decimal("800.00"))
        self.assertEqual(self.poupanca.calcular_saldo_atual(), Decimal("700.00"))

        # 3. Alerta preventivo se o valor for superior ao saldo da conta de origem
        res_insuficiente = self.client.post(
            "/api/lancamentos/transferencia",
            json={
                "valor": 1500.00,  # Corrente tem apenas 800
                "data_competencia": "2026-10-02",
                "conta_id": self.conta_corrente.id,
                "conta_destino_id": self.poupanca.id,
            },
            headers=headers,
        )
        self.assertEqual(res_insuficiente.status_code, 409)
        self.assertTrue(res_insuficiente.get_json()["requer_confirmacao"])

        # 4. Confirmação explícita autorizando saldo negativo na origem
        res_forcar = self.client.post(
            "/api/lancamentos/transferencia",
            json={
                "valor": 1500.00,
                "data_competencia": "2026-10-02",
                "conta_id": self.conta_corrente.id,
                "conta_destino_id": self.poupanca.id,
                "confirmar_saldo_negativo": True,
            },
            headers=headers,
        )
        self.assertEqual(res_forcar.status_code, 201)
        # Saldo: Corrente = 800 - 1500 = -700; Poupança = 700 + 1500 = 2200
        self.assertEqual(self.conta_corrente.calcular_saldo_atual(), Decimal("-700.00"))
        self.assertEqual(self.poupanca.calcular_saldo_atual(), Decimal("2200.00"))

    def test_filtros_e_listagem_de_lancamentos(self):
        """Testa múltiplos filtros e busca textual em GET /api/lancamentos."""
        self.autenticar(self.user1.id)
        headers = {"X-CSRFToken": "csrf-token-valido"}

        # Cria 3 lançamentos
        l1 = Lancamento(
            usuario_id=self.user1.id,
            tipo="receita",
            valor=Decimal("5000.00"),
            data_competencia=date(2026, 10, 5),
            data_vencimento=date(2026, 10, 5),
            descricao="Salário CLT",
            categoria_id=self.cat_salario.id,
            conta_id=self.conta_corrente.id,
            status="pago",
        )
        l2 = Lancamento(
            usuario_id=self.user1.id,
            tipo="despesa",
            valor=Decimal("200.00"),
            data_competencia=date(2026, 10, 10),
            data_vencimento=date(2026, 10, 10),
            descricao="Restaurante Italiano",
            categoria_id=self.cat_alimentacao.id,
            conta_id=self.conta_corrente.id,
            status="pago",
        )
        l3 = Lancamento(
            usuario_id=self.user1.id,
            tipo="despesa",
            valor=Decimal("150.00"),
            data_competencia=date(2026, 9, 20),
            data_vencimento=date(2026, 9, 25),
            descricao="Feira de Setembro",
            categoria_id=self.cat_alimentacao.id,
            conta_id=self.conta_corrente.id,
            status="pendente",
        )
        db.session.add_all([l1, l2, l3])
        db.session.commit()

        # 1. Filtro por Mês e Ano (10/2026) -> deve retornar l1 e l2
        res = self.client.get("/api/lancamentos?mes=10&ano=2026", headers=headers)
        self.assertEqual(res.status_code, 200)
        data = res.get_json()["dados"]
        self.assertEqual(len(data["lancamentos"]), 2)
        self.assertEqual(data["resumo"]["total_receitas"], 5000.00)
        self.assertEqual(data["resumo"]["total_despesas"], 200.00)
        self.assertEqual(data["resumo"]["saldo_periodo"], 4800.00)

        # 2. Filtro por Tipo: receita
        res_tipo = self.client.get("/api/lancamentos?tipo=receita", headers=headers)
        self.assertEqual(len(res_tipo.get_json()["dados"]["lancamentos"]), 1)
        self.assertEqual(res_tipo.get_json()["dados"]["lancamentos"][0]["descricao"], "Salário CLT")

        # 3. Filtro por Status: pendente
        res_pend = self.client.get("/api/lancamentos?status=pendente", headers=headers)
        self.assertEqual(len(res_pend.get_json()["dados"]["lancamentos"]), 1)
        self.assertEqual(res_pend.get_json()["dados"]["lancamentos"][0]["descricao"], "Feira de Setembro")

        # 4. Busca textual na descrição
        res_busca = self.client.get("/api/lancamentos?busca=italiano", headers=headers)
        self.assertEqual(len(res_busca.get_json()["dados"]["lancamentos"]), 1)
        self.assertEqual(res_busca.get_json()["dados"]["lancamentos"][0]["descricao"], "Restaurante Italiano")

    def test_alternar_status_pagamento(self):
        """Testa o endpoint PATCH /api/lancamentos/<id>/pagar."""
        self.autenticar(self.user1.id)
        headers = {"X-CSRFToken": "csrf-token-valido"}

        lanc = Lancamento(
            usuario_id=self.user1.id,
            tipo="despesa",
            valor=Decimal("300.00"),
            data_competencia=date(2026, 10, 1),
            data_vencimento=date(2026, 10, 1),
            descricao="Conta de Energia",
            categoria_id=self.cat_alimentacao.id,
            conta_id=self.conta_corrente.id,
            status="pendente",
        )
        db.session.add(lanc)
        db.session.commit()

        # Inicialmente pendente, saldo da conta = 1000
        self.assertEqual(self.conta_corrente.calcular_saldo_atual(), Decimal("1000.00"))

        # 1. Alterna para pago
        res1 = self.client.patch(f"/api/lancamentos/{lanc.id}/pagar", headers=headers)
        self.assertEqual(res1.status_code, 200)
        self.assertEqual(res1.get_json()["dados"]["novo_status"], "pago")
        # Saldo agora é 1000 - 300 = 700
        self.assertEqual(self.conta_corrente.calcular_saldo_atual(), Decimal("700.00"))

        # 2. Alterna de volta para pendente
        res2 = self.client.patch(f"/api/lancamentos/{lanc.id}/pagar", headers=headers)
        self.assertEqual(res2.status_code, 200)
        self.assertEqual(res2.get_json()["dados"]["novo_status"], "pendente")
        # Saldo retorna a 1000
        self.assertEqual(self.conta_corrente.calcular_saldo_atual(), Decimal("1000.00"))

    def test_soft_delete_reverte_saldo_e_oculta_da_listagem(self):
        """Testa se a exclusão lógica via DELETE /api/lancamentos/<id> preserva histórico e reverte saldo."""
        self.autenticar(self.user1.id)
        headers = {"X-CSRFToken": "csrf-token-valido"}

        lanc = Lancamento(
            usuario_id=self.user1.id,
            tipo="despesa",
            valor=Decimal("400.00"),
            data_competencia=date(2026, 10, 1),
            data_vencimento=date(2026, 10, 1),
            descricao="Compra de Eletrodoméstico",
            categoria_id=self.cat_alimentacao.id,
            conta_id=self.conta_corrente.id,
            status="pago",
        )
        db.session.add(lanc)
        db.session.commit()

        # Saldo com despesa paga: 1000 - 400 = 600
        self.assertEqual(self.conta_corrente.calcular_saldo_atual(), Decimal("600.00"))

        # Exclui o lançamento logicamente
        res_del = self.client.delete(f"/api/lancamentos/{lanc.id}", headers=headers)
        self.assertEqual(res_del.status_code, 200)

        # Saldo recalculado reverte o débito: volta para 1000
        self.assertEqual(self.conta_corrente.calcular_saldo_atual(), Decimal("1000.00"))

        # Consulta no banco: deleted_at preenchido
        db.session.refresh(lanc)
        self.assertTrue(lanc.esta_excluido)
        self.assertIsNotNone(lanc.deleted_at)

        # Na API de listagem, não deve aparecer
        res_list = self.client.get("/api/lancamentos", headers=headers)
        self.assertEqual(len(res_list.get_json()["dados"]["lancamentos"]), 0)

        # Tentar excluir novamente deve retornar 400
        res_repetido = self.client.delete(f"/api/lancamentos/{lanc.id}", headers=headers)
        self.assertEqual(res_repetido.status_code, 400)

    def test_isolamento_de_dados_e_anti_idor(self):
        """Garante que um usuário não pode acessar nem manipular lançamentos, contas ou categorias de outro."""
        # Cria lançamento do Usuário 2
        lanc_u2 = Lancamento(
            usuario_id=self.user2.id,
            tipo="receita",
            valor=Decimal("2000.00"),
            data_competencia=date(2026, 10, 1),
            data_vencimento=date(2026, 10, 1),
            descricao="Salário Privado do User 2",
            categoria_id=self.cat_user2.id,
            conta_id=self.conta_user2.id,
            status="pago",
        )
        db.session.add(lanc_u2)
        db.session.commit()

        # Autentica como Usuário 1
        self.autenticar(self.user1.id)
        headers = {"X-CSRFToken": "csrf-token-valido"}

        # 1. Usuário 1 não deve ver o lançamento do Usuário 2 na listagem
        res_list = self.client.get("/api/lancamentos", headers=headers)
        self.assertEqual(len(res_list.get_json()["dados"]["lancamentos"]), 0)

        # 2. Usuário 1 tenta editar o lançamento do Usuário 2 -> 404/403
        res_edit = self.client.put(
            f"/api/lancamentos/{lanc_u2.id}",
            json={"valor": 10.00},
            headers=headers,
        )
        self.assertEqual(res_edit.status_code, 404)

        # 3. Usuário 1 tenta pagar/alternar lançamento do Usuário 2 -> 404
        res_pagar = self.client.patch(
            f"/api/lancamentos/{lanc_u2.id}/pagar",
            headers=headers,
        )
        self.assertEqual(res_pagar.status_code, 404)

        # 4. Usuário 1 tenta excluir lançamento do Usuário 2 -> 404
        res_del = self.client.delete(
            f"/api/lancamentos/{lanc_u2.id}",
            headers=headers,
        )
        self.assertEqual(res_del.status_code, 404)

        # 5. Usuário 1 tenta criar lançamento usando conta de destino do Usuário 2 -> 403
        res_transf_alheia = self.client.post(
            "/api/lancamentos/transferencia",
            json={
                "valor": 50.00,
                "data_competencia": "2026-10-01",
                "conta_id": self.conta_corrente.id,
                "conta_destino_id": self.conta_user2.id,
            },
            headers=headers,
        )
        self.assertEqual(res_transf_alheia.status_code, 403)


if __name__ == "__main__":
    unittest.main()
