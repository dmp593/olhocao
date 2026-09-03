from django.db import migrations


MAPS_EMBED_URL = (
    "https://www.google.com/maps/embed?pb=!1m18!1m12!1m3!1d3069.5229487954834!"
    "2d-8.857333988379324!3d39.70542879850564!2m3!1f0!2f0!3f0!3m2!1i1024!"
    "2i768!4f13.1!3m3!1m2!1s0xd220b27073dc999%3A0x3b6d42f3a0673a84!"
    "2sOlh%C3%B3C%C3%A3o%20Centro%20Canino!5e0!3m2!1spt-PT!2spt!"
    "4v1744540891849!5m2!1spt-PT!2spt"
)


def seed_site_settings(apps):
    SiteSettings = apps.get_model("frontoffice", "SiteSettings")
    SiteSettings.objects.update_or_create(
        pk=1,
        defaults={
            "company_name": "Olhó Cão",
            "public_email": "olhocao@olhocao.pt",
            "primary_phone": "916 749 407",
            "secondary_phone": "924 454 382",
            "address_line_1": "Rua Quinta dos Frades, 904",
            "address_line_2": "2400-821 Leiria, Portugal",
            "maps_embed_url": MAPS_EMBED_URL,
            "directions_url": "https://maps.app.goo.gl/hMmN8RMx1bPk3uUj6",
            "weekday_hours": "08:30–12:30 / 15:30–19:30",
            "weekday_hours_en": "08:30–12:30 / 15:30–19:30",
            "weekday_hours_pt": "08:30–12:30 / 15:30–19:30",
            "weekend_hours": "Closed",
            "weekend_hours_en": "Closed",
            "weekend_hours_pt": "Encerrado",
            "home_services_eyebrow": "Everything in one place",
            "home_services_eyebrow_en": "Everything in one place",
            "home_services_eyebrow_pt": "Tudo num só lugar",
            "home_services_title": "Care for each part of your dog's routine",
            "home_services_title_en": "Care for each part of your dog's routine",
            "home_services_title_pt": "Cuidados para cada parte da rotina do seu cão",
            "home_services_subtitle": (
                "Simple online management for customers, with the operational detail "
                "our team needs."
            ),
            "home_services_subtitle_en": (
                "Simple online management for customers, with the operational detail "
                "our team needs."
            ),
            "home_services_subtitle_pt": (
                "Gestão online simples para os clientes, com o detalhe operacional de "
                "que a nossa equipa precisa."
            ),
            "home_testimonials_eyebrow": "What guides our work",
            "home_testimonials_eyebrow_en": "What guides our work",
            "home_testimonials_eyebrow_pt": "O que orienta o nosso trabalho",
            "home_testimonials_title": "Care that respects every dog",
            "home_testimonials_title_en": "Care that respects every dog",
            "home_testimonials_title_pt": "Cuidados que respeitam cada cão",
            "home_testimonials_subtitle": (
                "Clear communication, attentive supervision and activities adjusted "
                "to each dog's needs."
            ),
            "home_testimonials_subtitle_en": (
                "Clear communication, attentive supervision and activities adjusted "
                "to each dog's needs."
            ),
            "home_testimonials_subtitle_pt": (
                "Comunicação clara, supervisão atenta e atividades adaptadas às "
                "necessidades de cada cão."
            ),
            "about_eyebrow": "About us",
            "about_eyebrow_en": "About us",
            "about_eyebrow_pt": "Sobre nós",
            "about_title": "Our Story",
            "about_title_en": "Our Story",
            "about_title_pt": "A nossa história",
            "about_subtitle": "Passion for dogs since 2022",
            "about_subtitle_en": "Passion for dogs since 2022",
            "about_subtitle_pt": "Paixão por cães desde 2022",
            "about_history_title": "Olhó Cão History",
            "about_history_title_en": "Olhó Cão History",
            "about_history_title_pt": "História do Olhó Cão",
            "about_history": (
                "Founded on December 23, 2022, Olhó Cão is a dog training school "
                "focused on basic and advanced obedience, socialization, sports "
                "training, search and rescue, behavioral problem solving, and "
                "preparation for dog shows.\n\nThe center also offers dog hotel "
                "services, dog daycare (ATL), and through its brand LavóCão, provides "
                "bathing and grooming services.\n\nLocated in Azoia, the center "
                "primarily serves the Leiria and Marinha Grande areas.\n\nSuse Paixão "
                "and Luís Costa are the founders of the company, with Renato Vieira as "
                "the veterinary doctor responsible for the dogs' medical care."
            ),
            "about_history_en": (
                "Founded on December 23, 2022, Olhó Cão is a dog training school "
                "focused on basic and advanced obedience, socialization, sports "
                "training, search and rescue, behavioral problem solving, and "
                "preparation for dog shows.\n\nThe center also offers dog hotel "
                "services, dog daycare (ATL), and through its brand LavóCão, provides "
                "bathing and grooming services.\n\nLocated in Azoia, the center "
                "primarily serves the Leiria and Marinha Grande areas.\n\nSuse Paixão "
                "and Luís Costa are the founders of the company, with Renato Vieira as "
                "the veterinary doctor responsible for the dogs' medical care."
            ),
            "about_history_pt": (
                "Fundado a 23 de dezembro de 2022, o Olhó Cão é uma escola de treino "
                "canino dedicada à obediência básica e avançada, socialização, treino "
                "desportivo, busca e salvamento, resolução de problemas comportamentais "
                "e preparação para exposições caninas.\n\nO centro disponibiliza também "
                "hotel canino, ATL e, através da marca LavóCão, serviços de banho e "
                "grooming.\n\nLocalizado na Azoia, serve sobretudo as zonas de Leiria e "
                "Marinha Grande.\n\nSuse Paixão e Luís Costa são os fundadores da empresa, "
                "contando com Renato Vieira como médico veterinário responsável pelos "
                "cuidados de saúde dos cães."
            ),
            "facility_title": "Our Facility",
            "facility_title_en": "Our Facility",
            "facility_title_pt": "As nossas instalações",
            "facility_description": "A safe and stimulating environment for your dogs",
            "facility_description_en": "A safe and stimulating environment for your dogs",
            "facility_description_pt": "Um ambiente seguro e estimulante para os seus cães",
            "team_title": "Meet Our Trainers",
            "team_title_en": "Meet Our Trainers",
            "team_title_pt": "Conheça os nossos treinadores",
            "team_subtitle": "Passionate professionals dedicated to your dog's wellbeing",
            "team_subtitle_en": "Passionate professionals dedicated to your dog's wellbeing",
            "team_subtitle_pt": ("Profissionais apaixonados e dedicados ao bem-estar do seu cão"),
            "contact_eyebrow": "Contact",
            "contact_eyebrow_en": "Contact",
            "contact_eyebrow_pt": "Contacto",
            "contact_title": "Come visit us",
            "contact_title_en": "Come visit us",
            "contact_title_pt": "Venha visitar-nos",
            "contact_subtitle": "Your pup's home away from home",
            "contact_subtitle_en": "Your pup's home away from home",
            "contact_subtitle_pt": "A segunda casa do seu cão",
            "contact_visit_notice": (
                "Facility visits and classes are by appointment only. Please request "
                "an appointment in advance so we can ensure staff availability."
            ),
            "contact_visit_notice_en": (
                "Facility visits and classes are by appointment only. Please request "
                "an appointment in advance so we can ensure staff availability."
            ),
            "contact_visit_notice_pt": (
                "As visitas às instalações e as aulas funcionam apenas por marcação. "
                "Peça antecipadamente um horário para garantirmos a disponibilidade da equipa."
            ),
            "pricing_eyebrow": "Clear and current",
            "pricing_eyebrow_en": "Clear and current",
            "pricing_eyebrow_pt": "Claros e atuais",
            "pricing_title": "Pricing",
            "pricing_title_en": "Pricing",
            "pricing_title_pt": "Preços",
            "pricing_subtitle": (
                "Prices reflect the current Olhó Cão catalog and include VAT where applicable."
            ),
            "pricing_subtitle_en": (
                "Prices reflect the current Olhó Cão catalog and include VAT where applicable."
            ),
            "pricing_subtitle_pt": (
                "Os preços refletem o catálogo atual do Olhó Cão e incluem IVA quando aplicável."
            ),
        },
    )


def seed_slides(apps):
    HomeSlide = apps.get_model("frontoffice", "HomeSlide")
    if HomeSlide.objects.exists():
        return
    slides = (
        (
            "hero",
            "images/services/park-play.webp",
            "OlhóCão having fun!",
            "OlhóCão a divertir-se!",
            "A spacious outdoor setting where dogs can move, explore and socialize at their own pace.",
            "Um espaço exterior amplo onde os cães podem correr, explorar e socializar ao seu ritmo.",
            "Discover Our Services",
            "Descobrir os nossos serviços",
            "pricing",
        ),
        (
            "hero",
            "images/services/grooming.webp",
            "LavóCão so he smells nice!",
            "LavóCão para ficar cheiroso!",
            "Bath and grooming services adapted to the coat, skin, ears and nails of each dog.",
            "Serviços de banho e grooming adaptados ao pelo, pele, ouvidos e unhas de cada cão.",
            "Discover Our Services",
            "Descobrir os nossos serviços",
            "pricing",
        ),
        (
            "hero",
            "images/services/training.webp",
            "TreinóCão to be a good boy!",
            "TreinóCão para se portar bem!",
            "Structured training and obedience sessions with clear goals for each dog and handler.",
            "Sessões estruturadas de treino e obediência com objetivos claros para cada cão e tutor.",
            "Discover Our Services",
            "Descobrir os nossos serviços",
            "pricing",
        ),
        (
            "hero",
            "images/services/hotel-stay.webp",
            "DeixóCão: we take care while you take off!",
            "DeixóCão: nós cuidamos enquanto vai!",
            "Comfortable stays where routines, medication and care notes remain linked to each dog.",
            "Estadias confortáveis onde rotinas, medicação e cuidados ficam associados a cada cão.",
            "Discover Our Services",
            "Descobrir os nossos serviços",
            "pricing",
        ),
        (
            "benefit",
            "images/services/training.webp",
            "Training with clear goals",
            "Treino com objetivos claros",
            "Practical exercises, consistent cues and progress that customers can continue at home.",
            "Exercícios práticos, sinais consistentes e progresso que os tutores podem continuar em casa.",
            "",
            "",
            "",
        ),
        (
            "benefit",
            "images/services/park-play.webp",
            "Play and movement",
            "Brincadeira e movimento",
            "Activities adjusted to the session, the group and the needs recorded for each dog.",
            "Atividades adaptadas à sessão, ao grupo e às necessidades registadas de cada cão.",
            "",
            "",
            "",
        ),
        (
            "benefit",
            "images/services/enrichment.webp",
            "Socialization and cognitive development",
            "Socialização e desenvolvimento cognitivo",
            "Controlled-capacity sessions combine interaction, movement and appropriate enrichment.",
            "Sessões com capacidade controlada combinam interação, movimento e enriquecimento adequado.",
            "",
            "",
            "",
        ),
        (
            "benefit",
            "images/services/transport.webp",
            "More comfort for the whole family",
            "Mais conforto para toda a família",
            "When transport is included, collection and return details stay recorded with the booking.",
            "Quando há transporte, os detalhes de recolha e entrega ficam registados com a reserva.",
            "",
            "",
            "",
        ),
        (
            "benefit",
            "images/services/hotel-stay.webp",
            "Tailored for your dog's needs",
            "Adaptado às necessidades do seu cão",
            "Routines, health information and special instructions help the team prepare each stay.",
            "Rotinas, informação de saúde e instruções especiais ajudam a equipa a preparar cada estadia.",
            "",
            "",
            "",
        ),
    )
    HomeSlide.objects.bulk_create(
        [
            HomeSlide(
                kind=kind,
                static_image_path=image_path,
                image_alt=title_en,
                image_alt_en=title_en,
                image_alt_pt=title_pt,
                title=title_en,
                title_en=title_en,
                title_pt=title_pt,
                description=description_en,
                description_en=description_en,
                description_pt=description_pt,
                cta_text=cta_en,
                cta_text_en=cta_en,
                cta_text_pt=cta_pt,
                cta_destination=destination,
                order=order,
            )
            for order, (
                kind,
                image_path,
                title_en,
                title_pt,
                description_en,
                description_pt,
                cta_en,
                cta_pt,
                destination,
            ) in enumerate(slides)
        ]
    )


def seed_service_highlights(apps):
    ServiceHighlight = apps.get_model("frontoffice", "ServiceHighlight")
    if ServiceHighlight.objects.exists():
        return
    services = (
        (
            "hotel",
            "Dog hotel",
            "Hotel canino",
            "Overnight stays with each dog's care information available to the team.",
            "Estadias com a informação de cuidados de cada cão disponível para a equipa.",
            "Online booking\nOptional services per dog\nUpdates and private stay media",
            "Reserva online\nServiços opcionais por cão\nAtualizações e conteúdos privados da estadia",
            "Book hotel",
            "Reservar hotel",
            "hotel",
        ),
        (
            "daycare",
            "Dog daycare",
            "ATL canino",
            "Scheduled sessions with controlled capacity and configurable access.",
            "Sessões agendadas com capacidade controlada e acessos configuráveis.",
            "Session packs\nRecurring weekly plans\nCustomer-managed schedule",
            "Packs de sessões\nPlanos semanais recorrentes\nAgenda gerida pelo cliente",
            "See daycare plans",
            "Ver planos de ATL",
            "activities",
        ),
        (
            "training",
            "Training and obedience",
            "Treino e obediência",
            "Training services available as a session pack or recurring plan.",
            "Serviços de treino disponíveis em pack de sessões ou plano recorrente.",
            "Clear service level\nWeekly usage limits\nOnline reservations",
            "Nível de serviço claro\nLimites de utilização semanais\nReservas online",
            "Explore training",
            "Explorar treino",
            "activities",
        ),
        (
            "grooming",
            "Baths and grooming",
            "Banhos e grooming",
            "Care services managed centrally in the Olhó Cão catalog.",
            "Serviços de cuidado geridos centralmente no catálogo do Olhó Cão.",
            "Current prices\nDog-specific notes\nContact the team to schedule",
            "Preços atuais\nNotas específicas do cão\nContacte a equipa para agendar",
            "Contact us",
            "Contacte-nos",
            "contact",
        ),
    )
    ServiceHighlight.objects.bulk_create(
        [
            ServiceHighlight(
                icon=icon,
                title=title_en,
                title_en=title_en,
                title_pt=title_pt,
                description=description_en,
                description_en=description_en,
                description_pt=description_pt,
                features=features_en,
                features_en=features_en,
                features_pt=features_pt,
                action_text=action_en,
                action_text_en=action_en,
                action_text_pt=action_pt,
                destination=destination,
                order=order,
            )
            for order, (
                icon,
                title_en,
                title_pt,
                description_en,
                description_pt,
                features_en,
                features_pt,
                action_en,
                action_pt,
                destination,
            ) in enumerate(services)
        ]
    )


def seed_testimonials_and_team(apps):
    Testimonial = apps.get_model("frontoffice", "Testimonial")
    TeamMember = apps.get_model("frontoffice", "TeamMember")
    if not Testimonial.objects.exists():
        principles = (
            (
                "images/testimonials/golden-retriever.webp",
                "Individual attention",
                "Atenção individual",
                "Medication, allergies, routines and special notes stay visible to the operational team.",
                "Medicação, alergias, rotinas e notas especiais permanecem visíveis para a equipa operacional.",
            ),
            (
                "images/testimonials/white-shepherd.webp",
                "Safe socialization",
                "Socialização segura",
                "Session capacity and service compatibility are checked before each reservation is accepted.",
                "A capacidade da sessão e a compatibilidade do serviço são verificadas antes de cada reserva.",
            ),
            (
                "images/testimonials/border-collie.webp",
                "Simple communication",
                "Comunicação simples",
                "Customers manage bookings and plans online, while the team can assist whenever needed.",
                "Os clientes gerem reservas e planos online, com apoio da equipa sempre que necessário.",
            ),
        )
        Testimonial.objects.bulk_create(
            [
                Testimonial(
                    static_image_path=image_path,
                    image_alt=headline_en,
                    image_alt_en=headline_en,
                    image_alt_pt=headline_pt,
                    headline=headline_en,
                    headline_en=headline_en,
                    headline_pt=headline_pt,
                    body=body_en,
                    body_en=body_en,
                    body_pt=body_pt,
                    order=order,
                )
                for order, (image_path, headline_en, headline_pt, body_en, body_pt) in enumerate(
                    principles
                )
            ]
        )
    if not TeamMember.objects.exists():
        members = (
            (
                "Luís Costa",
                "frontoffice/about-us/trainers/luis.jpg",
                "Founder & CEO",
                "Fundador e CEO",
                "I am passionate about working with dogs and helping families achieve their training goals.",
                "Sou apaixonado por trabalhar com cães e por ajudar as famílias a alcançar os seus objetivos de treino.",
                "Specializes in obedience training",
                "Especialista em treino de obediência",
            ),
            (
                "Suse Paixão",
                "frontoffice/about-us/trainers/suse.jpg",
                "Founder & CEO",
                "Fundadora e CEO",
                "It is a privilege to see dogs and their handlers overcome difficulties and build a better relationship.",
                "É um privilégio ver cães e tutores ultrapassarem dificuldades e construírem uma relação melhor.",
                "Specializes in behavioral training",
                "Especialista em treino comportamental",
            ),
            (
                "Diogo Luz",
                "frontoffice/about-us/trainers/diogo.jpg",
                "Trainer",
                "Treinador",
                "With a degree in sports, I combine nature and physical activity to help families live in harmony.",
                "Com formação em desporto, combino natureza e atividade física para ajudar as famílias a viver em harmonia.",
                "Specializes in sports training",
                "Especialista em treino desportivo",
            ),
        )
        TeamMember.objects.bulk_create(
            [
                TeamMember(
                    name=name,
                    static_image_path=image_path,
                    image_alt=name,
                    image_alt_en=name,
                    image_alt_pt=name,
                    role=role_en,
                    role_en=role_en,
                    role_pt=role_pt,
                    description=description_en,
                    description_en=description_en,
                    description_pt=description_pt,
                    specialization=specialization_en,
                    specialization_en=specialization_en,
                    specialization_pt=specialization_pt,
                    order=order,
                )
                for order, (
                    name,
                    image_path,
                    role_en,
                    role_pt,
                    description_en,
                    description_pt,
                    specialization_en,
                    specialization_pt,
                ) in enumerate(members)
            ]
        )


def seed_partners_social_and_subjects(apps):
    Partner = apps.get_model("frontoffice", "Partner")
    SocialLink = apps.get_model("frontoffice", "SocialLink")
    ContactSubject = apps.get_model("frontoffice", "ContactSubject")
    if not Partner.objects.exists():
        Partner.objects.bulk_create(
            (
                Partner(
                    name="OutVets",
                    category="Our Veterinary",
                    category_en="Our Veterinary",
                    category_pt="O nosso veterinário",
                    website_url="https://outvets.pt/",
                    symbol="🏥",
                    order=0,
                ),
                Partner(
                    name="KaliuZ K9",
                    category="Material and Accessories",
                    category_en="Material and Accessories",
                    category_pt="Material e acessórios",
                    website_url="https://www.facebook.com/profile.php?id=100063537554454",
                    symbol="🛍️",
                    order=1,
                ),
                Partner(
                    name="Fortissimus Bellator",
                    category="Dog Breeder",
                    category_en="Dog Breeder",
                    category_pt="Criador de cães",
                    website_url="https://www.fortissimusbellator.pt/",
                    symbol="🐕",
                    tagline="#BreedingLegendaries",
                    tagline_en="#BreedingLegendaries",
                    tagline_pt="#BreedingLegendaries",
                    order=2,
                ),
            )
        )
    if not SocialLink.objects.exists():
        SocialLink.objects.bulk_create(
            (
                SocialLink(
                    platform="facebook",
                    url="https://www.facebook.com/treinocaninodanascentedoliz",
                    order=0,
                ),
                SocialLink(
                    platform="instagram",
                    url="https://www.instagram.com/olhocao_centrocanino/",
                    order=1,
                ),
            )
        )
    if not ContactSubject.objects.exists():
        subjects = (
            ("General", "Geral"),
            ("Dog Daycare", "ATL"),
            ("Training and Classes", "Treino e aulas"),
            ("Hotel Questions", "Questões sobre o hotel"),
            ("Visit Scheduling", "Agendamento de visita"),
        )
        ContactSubject.objects.bulk_create(
            [
                ContactSubject(
                    name=name_en,
                    name_en=name_en,
                    name_pt=name_pt,
                    order=order,
                )
                for order, (name_en, name_pt) in enumerate(subjects)
            ]
        )


def seed_public_website_content(apps, schema_editor):
    seed_site_settings(apps)
    seed_slides(apps)
    seed_service_highlights(apps)
    seed_testimonials_and_team(apps)
    seed_partners_social_and_subjects(apps)


class Migration(migrations.Migration):
    dependencies = [("frontoffice", "0004_alter_contactsubject_options_and_more")]

    operations = [migrations.RunPython(seed_public_website_content, migrations.RunPython.noop)]
